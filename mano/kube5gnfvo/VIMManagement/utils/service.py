import time

from VIMManagement.utils.kubernetes_api import KubernetesApi


class ServiceClient(KubernetesApi):
    def __init__(self, *args, **kwargs):
        self.service_type = kwargs['service_type'] if 'service_type' in kwargs else None
        self.ports = kwargs['ports'] if 'ports' in kwargs else None
        self.protocol = kwargs['protocol'] if 'protocol' in kwargs else None
        self.target_port = kwargs['target_port'] if 'target_port' in kwargs else None
        self.node_port = kwargs['node_port'] if 'node_port' in kwargs else None
        self.selector_name = kwargs['selector_name'] if 'selector_name' in kwargs else None
        self.headless_service = self._as_bool(kwargs.get('headless_service'))
        self.publish_not_ready_addresses = self._as_bool(
            kwargs.get('publish_not_ready_addresses'),
            default=self.headless_service,
        )
        super().__init__(*args, **kwargs)

    @staticmethod
    def _as_bool(value, default=False):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ('true', '1', 'yes', 'y')
        return bool(value)

    def read_resource(self, **kwargs):
        return self.core_v1.read_namespaced_service(self.instance_name, self.namespace)

    def create_resource(self, **kwargs):
        self.core_v1.create_namespaced_service(self.namespace, self.resource)

    def patch_resource(self, **kwargs):
        desired = self.instance_specific_resource()
        try:
            self.core_v1.patch_namespaced_service(self.instance_name, self.namespace, desired)
        except self.ApiException as e:
            if not self.headless_service or e.status not in (400, 422):
                raise

            self.delete_resource()
            for _ in range(10):
                try:
                    self.read_resource()
                    time.sleep(1)
                except self.ApiException as read_error:
                    if read_error.status == 404:
                        break
                    raise

            self.resource = desired
            self.create_resource()

    def delete_resource(self, **kwargs):
        self.core_v1.delete_namespaced_service(
            name=self.instance_name, namespace=self.namespace, body=self.delete_options)

    def instance_specific_resource(self, **kwargs):
        service = self.kubernetes_client.V1Service(api_version="v1", kind="Service")
        service_match_label={"app":self.instance_name}
        selector_name = self.selector_name or self.instance_name
        service.metadata = self.kubernetes_client.V1ObjectMeta(name=self.instance_name,labels=service_match_label)
        if self.node_port != None :
            #use cluster_ip='None' is not create Node_Port
            service.spec = self.kubernetes_client.V1ServiceSpec(
                selector={'app': selector_name}, ports=self._get_service_node_port(), type=self.service_type)
        else:
            spec_kwargs = {
                'selector': {'app': selector_name},
                'ports': self._get_service_port(),
                'type': self.service_type,
            }
            if self.headless_service:
                spec_kwargs['cluster_ip'] = 'None'
                spec_kwargs['publish_not_ready_addresses'] = self.publish_not_ready_addresses
            service.spec = self.kubernetes_client.V1ServiceSpec(**spec_kwargs)
        return service

    def _get_service_port(self):
        if self.protocol is None:
            protocol = 'TCP'
            return [self._create_service_port(protocol, port) for port in self.ports]
        else:
            # Handle case where protocol is a string instead of a list
            protocols = self.protocol if isinstance(self.protocol, list) else [self.protocol]
            service_port = list()
            for i, port in enumerate(self.ports):
                # Use corresponding protocol or default to TCP if not enough protocols
                protocol = protocols[i] if i < len(protocols) else 'TCP'
                service_port.append(self._create_service_port(protocol, port))
            return service_port
    def _get_service_node_port(self):
        if self.protocol is None:
            protocol = 'TCP'
            return [self._create_service_node_port(protocol, port, i) for i, port in enumerate(self.node_port)]
        else:
            # Handle case where protocol is a string instead of a list
            protocols = self.protocol if isinstance(self.protocol, list) else [self.protocol]
            service_port = list()
            for i, port in enumerate(self.node_port):
                # Use corresponding protocol or default to TCP if not enough protocols
                protocol = protocols[i] if i < len(protocols) else 'TCP'
                service_port.append(self._create_service_node_port(protocol, port, i))
            return service_port
    def _create_service_node_port(self, protocol, port, index):
        # Validate protocol
        valid_protocols = ['TCP', 'UDP', 'SCTP']
        if protocol not in valid_protocols:
            protocol = 'TCP'  # Default to TCP if invalid
        
        # Get the corresponding nodeport from the list
        node_port_value = self.node_port[index] if isinstance(self.node_port, list) and index < len(self.node_port) else self.node_port
        target_port_value = self.target_port[index] if isinstance(self.target_port, list) and index < len(self.target_port) else self.target_port
        
        # Build service port with optional nodePort (None means auto-assign)
        effective_port = port if port is not None else target_port_value
        service_port_kwargs = {
            'name': '{}{}'.format(self.instance_name[-10:], effective_port),
            'port': int(effective_port),
            'protocol': protocol
        }
        
        if target_port_value is not None:
            service_port_kwargs['target_port'] = int(target_port_value)
        
        if node_port_value is not None:
            service_port_kwargs['node_port'] = int(node_port_value)
        
        return self.kubernetes_client.V1ServicePort(**service_port_kwargs)

    def _create_service_port(self, protocol, port):
        # Validate protocol
        valid_protocols = ['TCP', 'UDP', 'SCTP']
        if protocol not in valid_protocols:
            protocol = 'TCP'  # Default to TCP if invalid
        return self.kubernetes_client.V1ServicePort(
            name='{}{}'.format(self.instance_name[-10:], port), port=int(port), protocol=protocol)
