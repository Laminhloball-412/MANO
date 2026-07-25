from pathlib import Path


path = Path(
    "/home/ubuntu/UI_huy_demo/static/js/app.js"
)

text = path.read_text(encoding="utf-8")

old_header = '''let requestInFlight = false;
'''

new_header = '''let requestInFlight = false;

const CONFIG_STORAGE_KEY = "ue_manager_config_v1";
'''

helper_marker = '''function collectConfig() {
'''

helper = '''function saveConfigToBrowser(config) {
  try {
    localStorage.setItem(
      CONFIG_STORAGE_KEY,
      JSON.stringify(config)
    );
    return true;
  } catch (error) {
    return false;
  }
}

function loadConfigFromBrowser() {
  try {
    const savedText = localStorage.getItem(
      CONFIG_STORAGE_KEY
    );

    if (!savedText) {
      return;
    }

    const savedConfig = JSON.parse(savedText);
    const fieldIds = [
      "imsi",
      "k",
      "opc",
      "imei",
      "apn",
      "sst",
      "sd"
    ];

    fieldIds.forEach((id) => {
      const input = document.getElementById(id);
      const value = savedConfig[id];

      if (
        input &&
        typeof value === "string"
      ) {
        input.value = value;
      }
    });
  } catch (error) {
    localStorage.removeItem(CONFIG_STORAGE_KEY);
  }
}

'''

old_response = '''    const data = await readJson(response);

    showToast(
'''

new_response = '''    const data = await readJson(response);

    if (data.success) {
      saveConfigToBrowser(config);
    }

    showToast(
'''

old_startup = '''updateStatus({
  state: "disconnected",
'''

new_startup = '''loadConfigFromBrowser();

updateStatus({
  state: "disconnected",
'''

replacements = [
    (old_header, new_header, "header"),
    (
        helper_marker,
        helper + helper_marker,
        "config helper marker"
    ),
    (
        old_response,
        new_response,
        "save response"
    ),
    (
        old_startup,
        new_startup,
        "startup"
    ),
]

for old, new, name in replacements:
    if old not in text:
        raise SystemExit(
            f"Unable to find {name}"
        )

    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
print("Config persistence patch applied")
