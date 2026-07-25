from pathlib import Path


path = Path("/home/ubuntu/UI_huy_demo/main.py")
text = path.read_text(encoding="utf-8")

if "def clear_log_queue():" in text:
    raise SystemExit("Log queue patch already exists")

update_conf_marker = "def update_conf(key, value, content):"

helper = '''def clear_log_queue():
    while True:
        try:
            log_queue.get_nowait()
        except queue.Empty:
            break


'''

if update_conf_marker not in text:
    raise SystemExit("update_conf marker not found")

text = text.replace(
    update_conf_marker,
    helper + update_conf_marker,
    1
)

old_attach = '''    ue_logs = []
    ue_status = {"state": "attaching", "ip": None}
'''

new_attach = '''    ue_logs = []
    clear_log_queue()
    ue_status = {"state": "attaching", "ip": None}
'''

if old_attach not in text:
    raise SystemExit("Attach log block not found")

text = text.replace(
    old_attach,
    new_attach,
    1
)

route_position = text.find(
    'def logs_stream():'
)

generate_position = text.find(
    '    def generate():',
    route_position
)

while_position = text.find(
    '        while True:',
    generate_position
)

if min(
    route_position,
    generate_position,
    while_position
) == -1:
    raise SystemExit("SSE generator not found")

text = (
    text[:generate_position]
    + '    def generate():\n'
    + text[while_position:]
)

path.write_text(text, encoding="utf-8")
print("Log queue patch applied")
