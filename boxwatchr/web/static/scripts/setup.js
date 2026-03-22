(function () {
    var testBtn = document.getElementById("test-btn");
    var saveBtn = document.getElementById("save-btn");
    var statusEl = document.getElementById("test-status");
    var folderSel = document.getElementById("imap_folder");
    var trashSel = document.getElementById("imap_trash_folder");
    var spamSel = document.getElementById("imap_spam_folder");

    function populateSelect(sel, folders, addAuto, selectedVal) {
        sel.innerHTML = "";
        if (addAuto) {
            var opt = document.createElement("option");
            opt.value = "";
            opt.textContent = "Auto-detect";
            if (!selectedVal) opt.selected = true;
            sel.appendChild(opt);
        }
        folders.forEach(function (f) {
            var opt = document.createElement("option");
            opt.value = f;
            opt.textContent = f;
            if (f === selectedVal) opt.selected = true;
            sel.appendChild(opt);
        });
        sel.disabled = false;
    }

    testBtn.addEventListener("click", function () {
        statusEl.textContent = "Testing...";
        statusEl.className = "small ms-2 text-secondary";
        testBtn.disabled = true;

        fetch("/api/test-imap", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": document.querySelector("[name='_csrf_token']").value },
            body: JSON.stringify({
                host: document.getElementById("imap_host").value,
                port: document.getElementById("imap_port").value,
                username: document.getElementById("imap_username").value,
                password: document.getElementById("imap_password").value,
                tls_mode: document.getElementById("tls_mode").value
            })
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            testBtn.disabled = false;
            if (data.success) {
                populateSelect(folderSel, data.folders, false, "");
                populateSelect(trashSel, data.folders, true, data.trash_folder || "");
                populateSelect(spamSel, data.folders, true, data.spam_folder || "");
                saveBtn.disabled = false;
                statusEl.textContent = "Connected. Select your watch folder below.";
                statusEl.className = "small ms-2 text-success";
            } else {
                statusEl.textContent = data.error || "Connection failed.";
                statusEl.className = "small ms-2 text-danger";
            }
        })
        .catch(function (e) {
            testBtn.disabled = false;
            statusEl.textContent = "Request failed: " + e;
            statusEl.className = "small ms-2 text-danger";
        });
    });
}());
