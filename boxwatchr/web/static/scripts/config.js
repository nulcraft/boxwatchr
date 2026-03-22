(function () {
    var testBtn = document.getElementById("test-btn");
    var statusEl = document.getElementById("test-status");
    var folderSel = document.getElementById("imap_folder");
    var trashSel = document.getElementById("imap_trash_folder");
    var spamSel = document.getElementById("imap_spam_folder");

    function populateSelect(sel, folders, currentVal, addAuto) {
        sel.innerHTML = "";
        if (addAuto) {
            var opt = document.createElement("option");
            opt.value = "";
            opt.textContent = "Auto-detect";
            if (!currentVal) opt.selected = true;
            sel.appendChild(opt);
        }
        folders.forEach(function (f) {
            var opt = document.createElement("option");
            opt.value = f;
            opt.textContent = f;
            if (f === currentVal) opt.selected = true;
            sel.appendChild(opt);
        });
    }

    testBtn.addEventListener("click", function () {
        statusEl.textContent = "Testing...";
        statusEl.className = "small ms-2 text-secondary";
        testBtn.disabled = true;

        var currentFolder = folderSel.options[folderSel.selectedIndex] ? folderSel.options[folderSel.selectedIndex].value : "";
        var currentTrash = trashSel.options[trashSel.selectedIndex] ? trashSel.options[trashSel.selectedIndex].value : "";
        var currentSpam = spamSel.options[spamSel.selectedIndex] ? spamSel.options[spamSel.selectedIndex].value : "";
        var csrfToken = document.querySelector("meta[name='csrf-token']").getAttribute("content");

        fetch("/api/test-imap", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
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
                populateSelect(folderSel, data.folders, currentFolder, false);
                populateSelect(trashSel, data.folders, currentTrash, true);
                populateSelect(spamSel, data.folders, currentSpam, true);
                statusEl.textContent = "Connected. Folder list updated.";
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
