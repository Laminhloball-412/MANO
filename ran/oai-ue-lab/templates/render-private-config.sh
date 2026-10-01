#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 <existing-srsue.conf> <template.conf> <private-output.conf>" >&2
  exit 2
fi

source_conf=$1
template=$2
output=$3

read_value() {
  local name=$1
  awk -F= -v key="$name" '
    $1 ~ "^[[:space:]]*" key "[[:space:]]*$" {
      value=$2
      sub(/[;#].*$/, "", value)
      gsub(/[[:space:]]/, "", value)
      gsub(/"/, "", value)
      print value
      exit
    }
  ' "$source_conf"
}

imsi=$(read_value imsi)
key=$(read_value k)
opc=$(read_value opc)

if [[ -z "$imsi" || -z "$key" || -z "$opc" ]]; then
  echo "could not read imsi/k/opc from source config" >&2
  exit 1
fi

umask 077
sed \
  -e "s/__IMSI__/$imsi/g" \
  -e "s/__KEY__/$key/g" \
  -e "s/__OPC__/$opc/g" \
  "$template" > "$output"

chmod 600 "$output"
echo "rendered private config for IMSI ending ${imsi: -4}: $output"
