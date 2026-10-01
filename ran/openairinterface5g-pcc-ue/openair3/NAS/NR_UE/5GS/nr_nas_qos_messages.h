/* SPDX-License-Identifier: LicenseRef-CSSL-1.0 */
#ifndef NR_NAS_QOS_MESSAGES_H
#define NR_NAS_QOS_MESSAGES_H
#include <stddef.h>
#include <stdint.h>
#include <string.h>

/* TS 24.501 sections 8.2.12, 8.3.2, 8.3.9-11. These views never own memory. */
typedef struct {
  const uint8_t *data;
  size_t length;
  uint8_t pdu_id, pti, type;
} nr_nas_sm_view_t;
typedef struct {
  const uint8_t *rules, *flows, *address;
  size_t rules_len, flows_len, address_len;
  uint8_t pdu_type;
} nr_nas_qos_view_t;

static inline size_t nr_nas_be16(const uint8_t *p) { return ((size_t)p[0]<<8)|p[1]; }

/* Input includes the seven-byte security header; caller has verified integrity
 * and decrypted the inner message. Bound SM parsing to payload length, not the
 * outer optional transport IEs. */
static inline int nr_nas_sm_container(const uint8_t *p, size_t n, nr_nas_sm_view_t *out)
{
  if (!p || !out || n < 17 || p[0]!=0x7e || p[1]!=2 || p[7]!=0x7e || p[8]!=0 ||
      p[9]!=0x68 || (p[10]&15)!=1) return -1;
  size_t length=nr_nas_be16(p+11);
  if(length<4 || length>n-13 || p[13]!=0x2e || p[14]==0 || p[14]>15) return -1;
  *out=(nr_nas_sm_view_t){.data=p+13,.length=length,.pdu_id=p[14],.pti=p[15],.type=p[16]};
  return 0;
}

/* Reject truncated flow descriptions before committing any classifier changes.
 * QoS characteristics configure the RAN; uplink classification uses their QFI. */
static inline int nr_nas_flow_description_bounds(const uint8_t *p, size_t n)
{
  while(n) {
    if(n<3 || (p[0]&63)==0 || (p[0]&0xc0)) return -1;
    unsigned op=p[1]>>5, count=p[2]&63;
    if(op<1 || op>3 || (p[1]&31) || (p[2]&0x80) || (op==2 && count)) return -1;
    p+=3;n-=3;
    for(unsigned i=0;i<count;i++) {
      if(n<2 || p[1]>n-2) return -1;
      size_t len=p[1]+2; p+=len;n-=len;
    }
  }
  return 0;
}

static inline int nr_nas_qos_optionals(const uint8_t *p, size_t n, nr_nas_qos_view_t *out, int modification)
{
  uint8_t seen[256]={0};
  while(n) {
    uint8_t ie=*p++;n--;
    if(ie&0x80) {
      if(modification || (ie>>4)!=8) return -1;
      continue;
    }
    if(seen[ie]++) return -1;
    if(ie==0x59 || ie==0x56) {
      if(n<1 || (modification && ie==0x56)) return -1;
      p++;n--;continue;
    }
    int extended=ie==0x7a || ie==0x79 || ie==0x7b || ie==0x75 || ie==0x78;
    int short_length=ie==0x2a || ie==0x29 || ie==0x22 || ie==0x25;
    if(!extended && !short_length) return -1;
    if(modification && ie!=0x7a && ie!=0x79 && ie!=0x7b && ie!=0x2a) return -1;
    size_t width=extended?2:1;
    if(n<width) return -1;
    size_t len=extended?nr_nas_be16(p):p[0];
    if(len>n-width) return -1;
    if(ie==0x7a) {
      if(out->rules || len==0) return -1;
      out->rules=p;out->rules_len=width+len;
    } else if(ie==0x79) {
      if(len<3 || nr_nas_flow_description_bounds(p+width,len)) return -1;
      out->flows=p;out->flows_len=width+len;
    } else if(ie==0x2a && len!=6) return -1;
    else if(ie==0x29) {
      if(len<1 || (p[width]&8)) return -1; /* SMF IPv6 link-local unsupported */
      uint8_t type=p[width]&7;
      size_t addr=type==1?4:type==2?8:type==3?12:0;
      if(!addr || len!=addr+1 || type!=out->pdu_type) return -1;
      out->address=p+width+1;out->address_len=addr;
    }
    p+=width+len;n-=width+len;
  }
  return 0;
}

static inline int nr_nas_modification_qos(const nr_nas_sm_view_t *sm, nr_nas_qos_view_t *out)
{
  if(!sm || !out || sm->length<4 || sm->data[3]!=0xcb) return -1;
  *out=(nr_nas_qos_view_t){0};
  return nr_nas_qos_optionals(sm->data+4,sm->length-4,out,1);
}

static inline int nr_nas_establishment_qos(const nr_nas_sm_view_t *sm, nr_nas_qos_view_t *out)
{
  if(!sm || !out || sm->length<8 || sm->data[3]!=0xc2) return -1;
  const uint8_t *p=sm->data+4;size_t n=sm->length-4;
  *out=(nr_nas_qos_view_t){.pdu_type=p[0]&15};p++;n--;
  size_t len=nr_nas_be16(p);
  if(len==0 || len>n-2) return -1;
  out->rules=p;out->rules_len=len+2;p+=len+2;n-=len+2;
  if(n<7 || p[0]!=6) return -1; /* mandatory Session-AMBR LV */
  p+=7;n-=7;
  return nr_nas_qos_optionals(p,n,out,0);
}

/* Plain UL NAS transport: SM response + PDU-session-ID only. No initial-request
 * type, DNN or S-NSSAI is appropriate for a modification response. */
static inline size_t nr_nas_sm_response(uint8_t *out,size_t cap,uint8_t pdu,uint8_t pti,uint8_t type,uint8_t cause)
{
  int optional_cause = cause && type == 0xd1; /* Release Request cause is TV */
  size_t sm_len=cause?(optional_cause?6:5):4, len=8+sm_len;
  if(!out || cap<len || pdu==0 || pdu>15) return 0;
  const uint8_t header[]={0x7e,0,0x67,1,0,(uint8_t)sm_len,0x2e,pdu,pti,type};
  memcpy(out,header,sizeof(header));
  if(optional_cause) { out[10]=0x59;out[11]=cause; }
  else if(cause) out[10]=cause;
  out[6+sm_len]=0x12;out[7+sm_len]=pdu;
  return len;
}
#endif
