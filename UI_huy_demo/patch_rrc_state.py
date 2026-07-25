from pathlib import Path


path = Path("/home/ubuntu/UI_huy_demo/main.py")
text = path.read_text(encoding="utf-8")

old = '''            elif "Received RRC Release" in line:
                ue_status["state"] = "connected"
                #ue_status["ip"] = None
'''

new = '''            elif "Received RRC Release" in line:
                if ue_status["state"] != "connected":
                    ue_status["state"] = "error"
                    add_log(
                        "[ERROR] RRC released before PDU session"
                    )
'''

if old not in text:
    raise SystemExit("RRC Release block not found")

path.write_text(
    text.replace(old, new, 1),
    encoding="utf-8"
)

print("RRC state patch applied")
