function getCsrfToken() {
    var meta = document.querySelector("meta[name='csrf-token']");
    return meta ? meta.content : "";
}

function showActionResult(message, isError) {
    var el = document.getElementById("action-result");
    if (!el) return;
    el.textContent = message;
    el.className = "alert py-2 px-3 small " + (isError ? "alert-danger" : "alert-success");
}

function runAction(actionType, extra) {
    var payload = Object.assign({ action: actionType }, extra || {});
    fetch("/emails/" + EMAIL_ID + "/action", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": getCsrfToken(),
        },
        body: JSON.stringify(payload),
    })
    .then(function(res) { return res.json().then(function(d) { return { ok: res.ok, data: d }; }); })
    .then(function(r) {
        if (r.ok && r.data.ok) {
            showActionResult("Action completed.", false);
        } else {
            showActionResult("Error: " + (r.data.error || "Unknown error"), true);
        }
    })
    .catch(function(err) {
        showActionResult("Request failed: " + err, true);
    });
}

document.addEventListener("DOMContentLoaded", function() {
    document.querySelectorAll("[data-email-action]").forEach(function(btn) {
        btn.addEventListener("click", function() {
            var action = this.dataset.emailAction;
            if (action === "move") {
                var dest = document.getElementById("move-dest");
                if (!dest || !dest.value) {
                    showActionResult("Select a destination folder.", true);
                    return;
                }
                runAction("move", { destination: dest.value });
            } else if (action === "add_label") {
                var labelInput = document.getElementById("label-value");
                if (!labelInput || !labelInput.value.trim()) {
                    showActionResult("Enter a label name.", true);
                    return;
                }
                runAction("add_label", { label: labelInput.value.trim() });
            } else {
                runAction(action);
            }
        });
    });
});
