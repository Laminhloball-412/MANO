#ifndef NR_UE_QOS_RULES_H
#define NR_UE_QOS_RULES_H
#include <stddef.h>
#include <stdint.h>
/* TS 24.501 QoS-rules IE, INCLUDING its network-order two-byte length.
 * Returns 0 on atomic success, -1 on unsupported/malformed/resource error.
 * Never mutate accepted state on failure. PDU IDs 1..15, QFIs 1..63.
 * Exactly one match-all default rule must survive; DQR identity and unique
 * precedence are validated. A default rule is removed only by session reset. Internal pthread lock protects all APIs.
 * Bounded: 32 UE/PDU contexts, 32 rules/context, 16 filters/rule, 8192-byte IE.
 * Supported UL components: match-all, IPv4 local/remote mask, protocol,
 * TCP/UDP local/remote single ports and ranges. Other components reject the IE.
 */
int nr_ue_qos_rules_apply(uint64_t ue_id, uint8_t pdu_id, const uint8_t *ie, size_t len);
/* Return selected QFI or -1/drop. Requires a valid installed DQR match-all rule.
 * default_qfi is retained for caller compatibility but NEVER substitutes for a
 * missing installed default rule. Packet is the complete inner IPv4 datagram. IPv6 and fragmented IPv4 drop.
 */
int nr_ue_qos_rules_classify(uint64_t ue_id, uint8_t pdu_id, const uint8_t *packet, size_t len, int default_qfi);
int nr_ue_qos_rules_default_qfi(uint64_t ue_id, uint8_t pdu_id);
void nr_ue_qos_rules_reset(uint64_t ue_id, uint8_t pdu_id);
#endif
