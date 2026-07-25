from pathlib import Path


path = Path("/home/ubuntu/UI_huy_demo/main.py")
text = path.read_text(encoding="utf-8")

marker = "\ndef run_ue():\n"

helper = r'''
def read_ue_ip():
    import time

    command = [
        "sudo", "ip", "netns", "exec", NETNS_NAME,
        "ip", "-4", "-o", "addr", "show",
        "dev", "tun_srsue1"
    ]

    for _ in range(10):
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=3
            )

            match = re.search(
                r"\binet\s+(\d+(?:\.\d+){3})/",
                result.stdout
            )

            if result.returncode == 0 and match:
                return match.group(1)

        except (subprocess.TimeoutExpired, OSError):
            pass

        time.sleep(0.25)

    return None
'''

old_block = '''            elif "PDU Session Establishment successful" in line:
                m = re.search(r"IP:\\s*(\\d+\\.\\d+\\.\\d+\\.\\d+)", line)
                if m:
                    ue_status["ip"] = m.group(1)
                ue_status["state"] = "connected"
'''

new_block = '''            elif "PDU Session Establishment successful" in line:
                m = re.search(r"IP:\\s*(\\d+\\.\\d+\\.\\d+\\.\\d+)", line)
                ue_status["state"] = "connected"

                if m:
                    ue_status["ip"] = m.group(1)
                else:
                    ue_status["ip"] = read_ue_ip()
'''

if "def read_ue_ip():" in text:
    raise SystemExit("read_ue_ip already exists")

if marker not in text:
    raise SystemExit("run_ue marker not found")

if old_block not in text:
    raise SystemExit("PDU status block not found")

text = text.replace(
    marker,
    helper + marker,
    1
)

text = text.replace(
    old_block,
    new_block,
    1
)

path.write_text(text, encoding="utf-8")
print("IP patch applied")
