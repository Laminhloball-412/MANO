#include "nr_ue_qos_rules.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static const unsigned char initial[] = {0,9, 1,0,6, 0x31,0x30,1,1,255,1};
static const unsigned char service[] = {0,22, 2,0,19, 0x21,0x30,14, 0x10,10,45,2,1,255,255,255,255, 0x30,17,0x50,0x13,0x88, 10,2};
static void packet(unsigned char *p, unsigned port) {
  memset(p,0,28); p[0]=0x45;p[3]=28;p[9]=17;
  p[12]=10;p[13]=45;p[14]=2;p[15]=2;
  p[16]=10;p[17]=45;p[18]=2;p[19]=1;
  p[20]=0xab;p[21]=0xcd;p[22]=port>>8;p[23]=port;p[25]=8;
}
int main(void) {
  unsigned char p[28];packet(p,5000);
  assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),9)==-1);
  assert(nr_ue_qos_rules_apply(1,1,initial,sizeof(initial))==0);
  assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),9)==1);
  assert(nr_ue_qos_rules_apply(1,1,service,sizeof(service))==0);
  assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),9)==2);
  packet(p,5001);assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),9)==1);
  unsigned char bad[sizeof(service)];memcpy(bad,service,sizeof(bad));bad[8]=0xfe;
  assert(nr_ue_qos_rules_apply(1,1,bad,sizeof(bad))<0);
  packet(p,5000);assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),9)==2);
  assert(nr_ue_qos_rules_apply(1,1,service,sizeof(service)-1)<0);
  assert(nr_ue_qos_rules_classify(2,1,p,sizeof(p),1)==-1);
  assert(nr_ue_qos_rules_classify(1,2,p,sizeof(p),1)==-1);
  nr_ue_qos_rules_reset(1,1);
  assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==-1);
  
  /* Every standardized operation; filters are matched by their own identifiers. */
  assert(nr_ue_qos_rules_apply(1,1,initial,sizeof(initial))==0);
  assert(nr_ue_qos_rules_apply(1,1,service,sizeof(service))==0);
  const unsigned char nochange[]={0,4,2,0,1,0xc0};
  assert(nr_ue_qos_rules_apply(1,1,nochange,sizeof(nochange))==0);
  assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==2);
  const unsigned char precedence_only[]={0,5,2,0,2,0xc0,7};
  assert(nr_ue_qos_rules_apply(1,1,precedence_only,sizeof(precedence_only))==0);
  assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==2);
  const unsigned char modify[]={0,6,2,0,3,0xc0,9,3};
  assert(nr_ue_qos_rules_apply(1,1,modify,sizeof(modify))==0);
  assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==3);
  const unsigned char replace[]={0,13,2,0,10,0x81,0x31,5,0x30,17,0x50,0x13,0x89,8,4};
  assert(nr_ue_qos_rules_apply(1,1,replace,sizeof(replace))==0);
  assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==1);
  packet(p,5001);assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==4);
  const unsigned char add[]={0,13,2,0,10,0x61,0x32,5,0x30,17,0x50,0x13,0x8a,8,4};
  assert(nr_ue_qos_rules_apply(1,1,add,sizeof(add))==0);
  packet(p,5002);assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==4);
  const unsigned char remove_filter[]={0,5,2,0,2,0xa1,2};
  assert(nr_ue_qos_rules_apply(1,1,remove_filter,sizeof(remove_filter))==0);
  assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==1);
  packet(p,5001);assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==4);
  assert(nr_ue_qos_rules_apply(1,1,remove_filter,sizeof(remove_filter))<0);
  const unsigned char remove_rule[]={0,4,2,0,1,0x40};
  assert(nr_ue_qos_rules_apply(1,1,remove_rule,sizeof(remove_rule))==0);
  assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==1);
  assert(nr_ue_qos_rules_default_qfi(1,1)==1);
  /* Downlink-only filters cannot select an uplink packet. */
  unsigned char dl[sizeof(service)];memcpy(dl,service,sizeof(dl));dl[6]=0x10;
  assert(nr_ue_qos_rules_apply(1,1,dl,sizeof(dl))==0);
  packet(p,5000);assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==1);
  nr_ue_qos_rules_reset(1,1);
  assert(nr_ue_qos_rules_apply(1,1,initial,sizeof(initial))==0);
  assert(nr_ue_qos_rules_apply(1,1,service,sizeof(service))==0);
  /* A later bad rule rolls back earlier valid changes in the same IE. */
  unsigned char transaction[sizeof(modify)+sizeof(service)-2];
  memcpy(transaction,modify,sizeof(modify));
  memcpy(transaction+sizeof(modify),service+2,sizeof(service)-2);
  transaction[1]=sizeof(transaction)-2;
  transaction[sizeof(modify)]=3;
  transaction[sizeof(modify)+6]=0xfe;
  assert(nr_ue_qos_rules_apply(1,1,transaction,sizeof(transaction))<0);
  assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==2);
  /* IPv4 remote address, protocol and malformed input isolation. */
  p[19]=9;assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==1);
  packet(p,5000);p[9]=1;assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==1);
  packet(p,5000);p[7]=1;assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==-1);
  packet(p,5000);assert(nr_ue_qos_rules_classify(1,1,p,27,1)==-1);
  const unsigned char remove_default[]={0,4,1,0,1,0x40};
  assert(nr_ue_qos_rules_apply(1,1,remove_default,sizeof(remove_default))<0);
  assert(nr_ue_qos_rules_default_qfi(1,1)==1);
  assert(nr_ue_qos_rules_classify(1,1,p,sizeof(p),1)==2);
  const unsigned char bad_dqr[]={0,6,2,0,3,0xd0,9,3};
  assert(nr_ue_qos_rules_apply(1,1,bad_dqr,sizeof(bad_dqr))<0);
  const unsigned char duplicate_precedence[]={0,6,2,0,3,0xc0,255,3};
  assert(nr_ue_qos_rules_apply(1,1,duplicate_precedence,sizeof(duplicate_precedence))<0);
  nr_ue_qos_rules_reset(1,1);
  unsigned char zero[sizeof(initial)];memcpy(zero,initial,sizeof(zero));zero[sizeof(zero)-1]=0;
  assert(nr_ue_qos_rules_apply(1,1,zero,sizeof(zero))<0);
  /* All truncated prefixes fail with no installed partial state. */
  for(size_t n=0;n<sizeof(initial);n++)assert(nr_ue_qos_rules_apply(1,1,initial,n)<0);
  assert(nr_ue_qos_rules_default_qfi(1,1)==-1);
  /* TCP local/remote IPv4 masks and directional port ranges. */
  assert(nr_ue_qos_rules_apply(1,1,initial,sizeof(initial))==0);
  const unsigned char tcp_rule[]={0,38,3,0,35,0x21,0x30,30,
    0x11,10,45,2,0,255,255,255,0,
    0x10,10,45,2,0,255,255,255,0,
    0x30,6,0x41,0xab,0xcc,0xab,0xd6,0x51,0x13,0x88,0x13,0x92,5,5};
  assert(nr_ue_qos_rules_apply(1,1,tcp_rule,sizeof(tcp_rule))==0);
  unsigned char tcp[40];memset(tcp,0,sizeof(tcp));packet(tcp,5000);tcp[3]=40;tcp[9]=6;tcp[32]=0x50;
  assert(nr_ue_qos_rules_classify(1,1,tcp,sizeof(tcp),1)==5);
  tcp[20]=1;assert(nr_ue_qos_rules_classify(1,1,tcp,sizeof(tcp),1)==1);
  tcp[20]=0xab;tcp[14]=9;assert(nr_ue_qos_rules_classify(1,1,tcp,sizeof(tcp),1)==1);
  nr_ue_qos_rules_reset(1,1);
  /* Capacity is explicit and reclaimed on session reset. */
  for(unsigned n=1;n<=32;n++)assert(nr_ue_qos_rules_apply(100+n,1,initial,sizeof(initial))==0);
  assert(nr_ue_qos_rules_apply(200,1,initial,sizeof(initial))<0);
  nr_ue_qos_rules_reset(101,1);
  assert(nr_ue_qos_rules_apply(200,1,initial,sizeof(initial))==0);
  for(unsigned n=1;n<=32;n++)nr_ue_qos_rules_reset(100+n,1);
  nr_ue_qos_rules_reset(200,1);
  puts("QoS rules tests passed: selection, directions, all operations, atomic rollback, isolation, malformed input, capacity");return 0;
}
