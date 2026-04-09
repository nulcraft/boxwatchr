(function () {
    var form = document.getElementById("training-form");
    var submitBtn = document.getElementById("training-submit");
    var errorEl = document.getElementById("training-error");
    var progressSection = document.getElementById("training-progress-section");
    var progressLabel = document.getElementById("training-progress-label");
    var progressCount = document.getElementById("training-progress-count");
    var progressFill = document.getElementById("training-progress-fill");
    var resultsBody = document.getElementById("training-results-body");

    var activeSource = null;

    function showError(msg) {
        errorEl.textContent = msg;
        errorEl.style.display = "";
    }

    function hideError() {
        errorEl.style.display = "none";
    }

    function setProgress(done, total) {
        var pct = total > 0 ? Math.round((done / total) * 100) : 0;
        progressFill.style.width = pct + "%";
        progressFill.setAttribute("aria-valuenow", pct);
        progressCount.textContent = done + " / " + total;
    }

    function addRow(subject, date, result, success) {
        var tr = document.createElement("tr");

        var tdSubject = document.createElement("td");
        tdSubject.textContent = subject || "";
        tdSubject.className = "training-col-subject";

        var tdDate = document.createElement("td");
        tdDate.textContent = date || "";
        tdDate.className = "col-date";

        var tdResult = document.createElement("td");
        tdResult.className = "training-col-result";

        var badge = document.createElement("span");
        if (result === "trained") {
            badge.className = "badge text-bg-success";
        } else if (result && result.indexOf("skipped") === 0) {
            badge.className = "badge text-bg-secondary";
        } else {
            badge.className = "badge text-bg-danger";
        }
        badge.textContent = result || "";
        tdResult.appendChild(badge);

        tr.appendChild(tdSubject);
        tr.appendChild(tdDate);
        tr.appendChild(tdResult);

        resultsBody.insertBefore(tr, resultsBody.firstChild);
    }

    function finishProgress(label) {
        progressFill.classList.remove("progress-bar-animated");
        progressFill.style.width = "100%";
        progressLabel.textContent = label;
    }

    form.addEventListener("submit", function (e) {
        e.preventDefault();
        hideError();

        if (activeSource) {
            activeSource.close();
            activeSource = null;
        }

        var folder = document.getElementById("training-folder").value;
        var learnType = document.getElementById("training-type").value;
        var csrfToken = document.querySelector("meta[name='csrf-token']").getAttribute("content");

        submitBtn.disabled = true;
        progressSection.style.display = "";
        resultsBody.innerHTML = "";
        progressFill.classList.add("progress-bar-animated");
        progressFill.style.width = "0%";
        progressLabel.textContent = "Connecting...";
        progressCount.textContent = "";

        var formData = new FormData();
        formData.append("folder", folder);
        formData.append("learn_type", learnType);
        formData.append("_csrf_token", csrfToken);

        fetch("/api/training/start", {
            method: "POST",
            headers: { "X-CSRF-Token": csrfToken },
            body: formData
        })
        .then(function (r) {
            if (!r.ok) throw new Error("Server error " + r.status);
            return r.json();
        })
        .then(function (data) {
            var jobId = data.job_id;
            var source = new EventSource("/api/training/stream/" + jobId);
            activeSource = source;

            source.onmessage = function (e) {
                var msg;
                try {
                    msg = JSON.parse(e.data);
                } catch (_) {
                    return;
                }

                if (msg.type === "start") {
                    var typeLabel = learnType === "spam" ? "spam" : "ham";
                    progressLabel.textContent = "Training " + msg.total + " message(s) from \"" + folder + "\" as " + typeLabel + "...";
                    setProgress(0, msg.total);
                } else if (msg.type === "progress") {
                    setProgress(msg.done, msg.total);
                    addRow(msg.subject, msg.date, msg.result, msg.success);
                } else if (msg.type === "done") {
                    source.close();
                    activeSource = null;
                    setProgress(msg.trained + msg.skipped + msg.failed, msg.trained + msg.skipped + msg.failed);
                    finishProgress(
                        "Done. Trained: " + msg.trained + ", skipped: " + msg.skipped + ", failed: " + msg.failed + "."
                    );
                    submitBtn.disabled = false;
                } else if (msg.type === "error") {
                    source.close();
                    activeSource = null;
                    finishProgress("Error.");
                    showError(msg.message || "An error occurred.");
                    submitBtn.disabled = false;
                }
            };

            source.onerror = function () {
                source.close();
                activeSource = null;
                finishProgress("Connection lost.");
                showError("Lost connection to the server during training.");
                submitBtn.disabled = false;
            };
        })
        .catch(function (e) {
            showError("Failed to start training: " + e);
            submitBtn.disabled = false;
            progressSection.style.display = "none";
        });
    });
}());
