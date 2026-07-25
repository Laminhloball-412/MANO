from pathlib import Path


path = Path(
    "/home/ubuntu/UI_huy_demo/static/js/app.js"
)

text = path.read_text(encoding="utf-8")

if "Cancel connection" in text:
    raise SystemExit("Cancel button patch already exists")

start_marker = "function updateActionButtons(state) {"
end_marker = "function updateStatus(data) {"

start = text.find(start_marker)
end = text.find(end_marker, start)

if start == -1 or end == -1:
    raise SystemExit(
        "Unable to find updateActionButtons function"
    )

new_function = '''function updateActionButtons(state) {
  const isConnected = state === "connected";
  const isTransitioning =
    TRANSITION_STATES.includes(state);

  const showDetach =
    isConnected || isTransitioning;

  attachButton.style.display = showDetach
    ? "none"
    : "inline-flex";

  detachButton.style.display = showDetach
    ? "inline-flex"
    : "none";

  attachButton.disabled = requestInFlight;
  detachButton.disabled = requestInFlight;

  if (
    state === "failed" ||
    state === "error"
  ) {
    attachButton.innerHTML =
      "<span>↻</span> Retry attach";
  } else {
    attachButton.innerHTML =
      "<span>▶</span> Attach UE";
  }

  if (isTransitioning) {
    detachButton.innerHTML =
      "<span>■</span> Cancel connection";
  } else {
    detachButton.innerHTML =
      "<span>■</span> Detach UE";
  }
}

'''

text = (
    text[:start]
    + new_function
    + text[end:]
)

path.write_text(text, encoding="utf-8")
print("Cancel button patch applied")
