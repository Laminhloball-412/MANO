/* SPDX-License-Identifier: LicenseRef-CSSL-1.0 */
#include "nr_nas_qos_messages.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
int main(void) {
  /* Full protected DL NAS transport, decrypted inner payload: one rules IE. */
  uint8_t dl[] = {0x7e,2,0,0,0,0,1,0x7e,0,0x68,1,0,10,
                  0x2e,10,0,0xcb,0x7a,0,3,1,2,3,0x12,10};
  nr_nas_sm_view_t sm = {0}; nr_nas_qos_view_t qos = {0};
  assert(nr_nas_sm_container(dl,sizeof(dl),&sm)==0);
  assert(sm.pdu_id==10 && sm.pti==0 && sm.type==0xcb && sm.length==10);
  assert(nr_nas_modification_qos(&sm,&qos)==0);
  assert(qos.rules_len==5 && qos.rules[2]==1);
  for(size_t n=0;n<23;n++) assert(nr_nas_sm_container(dl,n,&sm)!=0);
  dl[1]=0; assert(nr_nas_sm_container(dl,sizeof(dl),&sm)!=0); dl[1]=2;
  dl[12]=255; assert(nr_nas_sm_container(dl,sizeof(dl),&sm)!=0); dl[12]=10;
  dl[10]=2; assert(nr_nas_sm_container(dl,sizeof(dl),&sm)!=0); dl[10]=1;
  assert(nr_nas_sm_container(dl,sizeof(dl),&sm)==0);
  dl[19]=255; assert(nr_nas_modification_qos(&sm,&qos)!=0); dl[19]=3;
  uint8_t duplicate[]={0x2e,10,0,0xcb,0x7a,0,1,0,0x7a,0,1,0};
  sm=(nr_nas_sm_view_t){.data=duplicate,.length=sizeof(duplicate)};
  assert(nr_nas_modification_qos(&sm,&qos)!=0);
  uint8_t tail[]={0x2e,10,0,0xcb,0x59};
  sm=(nr_nas_sm_view_t){.data=tail,.length=sizeof(tail)};
  assert(nr_nas_modification_qos(&sm,&qos)!=0);
  uint8_t unsupported[]={0x2e,10,0,0xcb,0x75,0,1,0};
  sm=(nr_nas_sm_view_t){.data=unsupported,.length=sizeof(unsupported)};
  assert(nr_nas_modification_qos(&sm,&qos)!=0);
  uint8_t accept[]={0x2e,10,1,0xc2,0x11,0,3,1,2,3,6,1,0,1,1,0,1,0x29,5,1,10,45,2,9};
  sm=(nr_nas_sm_view_t){.data=accept,.length=sizeof(accept)};
  assert(nr_nas_establishment_qos(&sm,&qos)==0);
  assert(qos.rules_len==5 && qos.pdu_type==1 && qos.address_len==4 && qos.address[3]==9);
  for(size_t n=0;n<17;n++){sm.length=n;assert(nr_nas_establishment_qos(&sm,&qos)!=0);}
  sm.length=sizeof(accept)-1; assert(nr_nas_establishment_qos(&sm,&qos)!=0);
  uint8_t flows[]={0x2e,10,0,0xcb,0x79,0,6,2,0x20,1,1,1,3};
  sm=(nr_nas_sm_view_t){.data=flows,.length=sizeof(flows)};
  assert(nr_nas_modification_qos(&sm,&qos)==0 && qos.flows_len==8);
  for(size_t n=5;n<sizeof(flows);n++){sm.length=n;assert(nr_nas_modification_qos(&sm,&qos)!=0);}
  sm.length=sizeof(flows);flows[11]=2;assert(nr_nas_modification_qos(&sm,&qos)!=0);
  uint8_t out[16];
  assert(nr_nas_sm_response(out,sizeof(out),10,0,0xcc,0)==12);
  const uint8_t complete[]={0x7e,0,0x67,1,0,4,0x2e,10,0,0xcc,0x12,10};
  assert(memcmp(out,complete,sizeof(complete))==0);
  assert(nr_nas_sm_response(out,sizeof(out),10,0,0xcd,84)==13 && out[10]==84);
  assert(nr_nas_sm_response(out,11,10,0,0xcc,0)==0);
  assert(nr_nas_sm_response(out,sizeof(out),10,1,0xd1,84)==14);
  assert(out[10]==0x59 && out[11]==84 && out[12]==0x12 && out[13]==10);
  puts("NAS QoS message bounds and response tests passed");
}
