/* Bounded UE QoS classifier for standards-signalled rules; no service defaults. */
#include "nr_ue_qos_rules.h"
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#define CONTEXTS 32
#define RULES 32
#define FILTERS 16
#define MATCH_ALL 1u
#define REMOTE_IP 2u
#define LOCAL_IP 4u
#define PROTOCOL 8u
#define LOCAL_PORT 16u
#define REMOTE_PORT 32u
struct filter {
  uint8_t id, direction, protocol;
  unsigned fields;
  uint32_t local_ip, local_mask, remote_ip, remote_mask;
  uint16_t local_lo, local_hi, remote_lo, remote_hi;
};
struct rule {
  bool used, dqr;
  uint8_t id, qfi, precedence, count;
  struct filter filters[FILTERS];
};
struct context {
  bool used;
  uint64_t ue;
  uint8_t pdu;
  struct rule rules[RULES];
};
static struct context contexts[CONTEXTS];
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static uint16_t u16(const uint8_t *p) { return ((uint16_t)p[0] << 8) | p[1]; }
static uint32_t u32(const uint8_t *p) { return ((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3]; }
static struct context *find_context(uint64_t ue, uint8_t pdu) {
  for (unsigned i=0;i<CONTEXTS;i++) if (contexts[i].used && contexts[i].ue==ue && contexts[i].pdu==pdu) return &contexts[i];
  return NULL;
}
static struct rule *find_rule(struct context *ctx, uint8_t id) {
  for (unsigned i=0;i<RULES;i++) if (ctx->rules[i].used && ctx->rules[i].id==id) return &ctx->rules[i];
  return NULL;
}
static int filter_index(const struct rule *r, uint8_t id) {
  for (unsigned i=0;i<r->count;i++) if (r->filters[i].id==id) return (int)i;
  return -1;
}
static int parse_filter(struct filter *f, const uint8_t **cursor, const uint8_t *end) {
  const uint8_t *p=*cursor;
  if (end-p<2 || (p[0]&0xc0)) return -1;
  f->id=p[0]&15; f->direction=(p[0]>>4)&3;
  size_t length=p[1]; p+=2;
  if (!f->direction || !length || (size_t)(end-p)<length) return -1;
  const uint8_t *stop=p+length;
  while (p<stop) {
    uint8_t type=*p++;
    unsigned bit;
    size_t bytes;
    switch(type) {
      case 0x01: bit=MATCH_ALL;bytes=0;break;
      case 0x10: bit=REMOTE_IP;bytes=8;break;
      case 0x11: bit=LOCAL_IP;bytes=8;break;
      case 0x30: bit=PROTOCOL;bytes=1;break;
      case 0x40: bit=LOCAL_PORT;bytes=2;break;
      case 0x41: bit=LOCAL_PORT;bytes=4;break;
      case 0x50: bit=REMOTE_PORT;bytes=2;break;
      case 0x51: bit=REMOTE_PORT;bytes=4;break;
      default:return -1;
    }
    if ((size_t)(stop-p)<bytes || (f->fields&bit)) return -1;
    f->fields|=bit;
    switch(bit) {
      case REMOTE_IP:f->remote_ip=u32(p);f->remote_mask=u32(p+4);break;
      case LOCAL_IP:f->local_ip=u32(p);f->local_mask=u32(p+4);break;
      case PROTOCOL:f->protocol=*p;break;
      case LOCAL_PORT:f->local_lo=u16(p);f->local_hi=bytes==4?u16(p+2):f->local_lo;if(f->local_lo>f->local_hi)return -1;break;
      case REMOTE_PORT:f->remote_lo=u16(p);f->remote_hi=bytes==4?u16(p+2):f->remote_lo;if(f->remote_lo>f->remote_hi)return -1;break;
      default:break;
    }
    p+=bytes;
  }
  if ((f->fields&MATCH_ALL) && f->fields!=MATCH_ALL) return -1;
  /* A port component is unambiguous only for explicitly signalled TCP/UDP. */
  if ((f->fields&(LOCAL_PORT|REMOTE_PORT)) && (!(f->fields&PROTOCOL) || (f->protocol!=6 && f->protocol!=17))) return -1;
  *cursor=stop;
  return 0;
}
static bool valid_default(const struct rule *r) {
  return r->used && r->dqr && r->count==1 && r->filters[0].fields==MATCH_ALL && (r->filters[0].direction&2);
}
static int parse_rules(struct context *ctx, const uint8_t *p, const uint8_t *end) {
  while(p<end) {
    if(end-p<4)return -1;
    uint8_t id=*p++;size_t length=u16(p);p+=2;
    if(!id || !length || (size_t)(end-p)<length)return -1;
    const uint8_t *stop=p+length;
    uint8_t flags=*p++, op=flags>>5, count=flags&15;
    bool dqr=(flags&16)!=0;
    struct rule *r=find_rule(ctx,id);
    if(op==2) {
      if(!r || r->dqr || count || p!=stop)return -1;
      memset(r,0,sizeof(*r));continue;
    }
    if(op<1 || op>6)return -1;
    if(op==1) {
      if(r || !count)return -1;
      for(unsigned i=0;i<RULES;i++)if(!ctx->rules[i].used){r=&ctx->rules[i];break;}
      if(!r)return -1;
      memset(r,0,sizeof(*r));r->used=true;r->id=id;r->dqr=dqr;
    } else if(!r || r->dqr!=dqr) return -1;
    if(op==6 && count)return -1;
    if((op==3 || op==4 || op==5) && !count)return -1;
    if(op==4) {r->count=0;memset(r->filters,0,sizeof(r->filters));}
    for(unsigned i=0;i<count;i++) {
      if(op==5) {
        if(p==stop || (*p&0xf0))return -1;
        int index=filter_index(r,*p++);
        if(index<0)return -1;
        memmove(&r->filters[index],&r->filters[index+1],(r->count-(unsigned)index-1)*sizeof(r->filters[0]));
        r->count--;
      } else {
        struct filter f={0};
        if(parse_filter(&f,&p,stop)<0 || r->count>=FILTERS || filter_index(r,f.id)>=0)return -1;
        r->filters[r->count++]=f;
      }
    }
    if(!r->count)return -1;
    if(stop-p==0 && op!=1)continue; /* retain prior optional precedence/QFI */
    if(stop-p>2 || (op==1 && stop-p!=2))return -1;
    r->precedence=*p++;
    if(p==stop)continue; /* modification may change precedence alone */
    uint8_t qfi=*p++;
    if((qfi&0x80) || !(qfi&63))return -1; /* bit 7 segregation permitted; bit 8 spare */
    r->qfi=qfi&63;
  }
  unsigned defaults=0;
  for(unsigned i=0;i<RULES;i++)if(ctx->rules[i].used && ctx->rules[i].dqr) {
    if(!valid_default(&ctx->rules[i]))return -1;
    defaults++;
  }
  for(unsigned i=0;i<RULES;i++)if(ctx->rules[i].used)
    for(unsigned j=i+1;j<RULES;j++)if(ctx->rules[j].used && ctx->rules[i].precedence==ctx->rules[j].precedence)return -1;
  return defaults==1?0:-1;
}
int nr_ue_qos_rules_apply(uint64_t ue, uint8_t pdu, const uint8_t *ie, size_t len) {
  if(!pdu || pdu>15 || !ie || len<2 || len>8192 || u16(ie)!=len-2 || len==2)return -1;
  struct context *candidate=malloc(sizeof(*candidate));
  if(!candidate)return -1;
  pthread_mutex_lock(&lock);
  struct context *current=find_context(ue,pdu), *slot=current;
  if(current)memcpy(candidate,current,sizeof(*candidate));else{
    memset(candidate,0,sizeof(*candidate));candidate->used=true;candidate->ue=ue;candidate->pdu=pdu;
    for(unsigned i=0;i<CONTEXTS;i++)if(!contexts[i].used){slot=&contexts[i];break;}
  }
  int status=slot?parse_rules(candidate,ie+2,ie+len):-1;
  if(status==0)memcpy(slot,candidate,sizeof(*slot));
  pthread_mutex_unlock(&lock);free(candidate);return status;
}
static bool matches(const struct filter *f,const uint8_t *packet,size_t len) {
  if(!(f->direction&2))return false;
  unsigned ihl=(packet[0]&15)*4;
  uint8_t proto=packet[9];
  if((f->fields&PROTOCOL) && f->protocol!=proto)return false;
  if((f->fields&LOCAL_IP) && (u32(packet+12)&f->local_mask)!=(f->local_ip&f->local_mask))return false;
  if((f->fields&REMOTE_IP) && (u32(packet+16)&f->remote_mask)!=(f->remote_ip&f->remote_mask))return false;
  if(f->fields&(LOCAL_PORT|REMOTE_PORT)) {
    if((proto!=6 && proto!=17) || len<ihl+4)return false;
    uint16_t local=u16(packet+ihl),remote=u16(packet+ihl+2);
    if((f->fields&LOCAL_PORT) && (local<f->local_lo || local>f->local_hi))return false;
    if((f->fields&REMOTE_PORT) && (remote<f->remote_lo || remote>f->remote_hi))return false;
  }
  return true;
}
int nr_ue_qos_rules_classify(uint64_t ue,uint8_t pdu,const uint8_t *packet,size_t len,int default_qfi) {
  (void)default_qfi;
  if(!packet || len<20 || packet[0]>>4!=4)return -1;
  unsigned ihl=(packet[0]&15)*4;
  size_t total=u16(packet+2);
  if(ihl<20 || ihl>len || total<ihl || total>len || (u16(packet+6)&0x3fff))return -1;
  if(packet[9]==17 && total<ihl+8)return -1;
  if(packet[9]==6 && total<ihl+20)return -1;
  pthread_mutex_lock(&lock);
  struct context *ctx=find_context(ue,pdu);
  int selected=-1,precedence=256,id=256;
  bool has_default=false;
  if(ctx)for(unsigned i=0;i<RULES;i++)if(valid_default(&ctx->rules[i]))has_default=true;
  if(ctx && has_default)for(unsigned i=0;i<RULES;i++) {
    struct rule *r=&ctx->rules[i];
    if(!r->used || r->precedence>precedence || (r->precedence==precedence && r->id>=id))continue;
    for(unsigned j=0;j<r->count;j++)if(matches(&r->filters[j],packet,total)){
      selected=r->qfi;precedence=r->precedence;id=r->id;break;
    }
  }
  pthread_mutex_unlock(&lock);return selected;
}
void nr_ue_qos_rules_reset(uint64_t ue,uint8_t pdu) {
  pthread_mutex_lock(&lock);struct context *ctx=find_context(ue,pdu);
  if(ctx)memset(ctx,0,sizeof(*ctx));
  pthread_mutex_unlock(&lock);
}

int nr_ue_qos_rules_default_qfi(uint64_t ue,uint8_t pdu) {
  pthread_mutex_lock(&lock);
  struct context *ctx=find_context(ue,pdu);
  int qfi=-1;
  if(ctx)for(unsigned i=0;i<RULES;i++)if(valid_default(&ctx->rules[i])){qfi=ctx->rules[i].qfi;break;}
  pthread_mutex_unlock(&lock);
  return qfi;
}
