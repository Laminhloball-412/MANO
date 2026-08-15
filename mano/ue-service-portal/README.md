# ANSA UE & Service Portal

Standalone Django UX for UE profile management, workload staging, and
pre-decision measurements. It intentionally does not import or modify the
Free5GMANO WebUI.

The initial version is a safe UX prototype:

- reads existing `ue*.conf` files when deployed on the lab VM;
- stores newly added UE profiles as local drafts;
- models multiple allowed S-NSSAIs and one requested S-NSSAI;
- previews deterministic sample measurement layouts;
- keeps subscriber provisioning, UE execution, and automatic slice selection
  disabled until their adapters are implemented.
