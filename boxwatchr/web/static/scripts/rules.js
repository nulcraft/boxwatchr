function _levenshtein(a, b) {
    var m = a.length, n = b.length;
    var prev = [], curr = [];
    for (var j = 0; j <= n; j++) prev[j] = j;
    for (var i = 1; i <= m; i++) {
        curr[0] = i;
        for (var j = 1; j <= n; j++) {
            curr[j] = a[i - 1] === b[j - 1]
                ? prev[j - 1]
                : 1 + Math.min(prev[j], curr[j - 1], prev[j - 1]);
        }
        var tmp = prev; prev = curr; curr = tmp;
    }
    return prev[n];
}

function _fuzzyMatch(query, target) {
    query = query.toLowerCase();
    target = target.toLowerCase();

    if (target.includes(query)) return true;

    // Subsequence: all query chars appear in order in target
    var qi = 0;
    for (var ti = 0; ti < target.length && qi < query.length; ti++) {
        if (target[ti] === query[qi]) qi++;
    }
    if (qi === query.length) return true;

    // Per-word Levenshtein for typo tolerance (queries of 4+ chars only)
    if (query.length >= 4) {
        var threshold = Math.floor(query.length / 4);
        var words = target.split(/[\s_\-]+/);
        for (var i = 0; i < words.length; i++) {
            if (_levenshtein(query, words[i]) <= threshold) return true;
        }
    }

    return false;
}

document.addEventListener("DOMContentLoaded", function() {
    // Export overlay
    var exportBtn = document.getElementById("export-btn");
    var exportOverlay = document.getElementById("export-overlay");
    var exportClose = document.getElementById("export-close");
    var exportTextarea = document.getElementById("export-json-text");

    if (exportBtn && exportOverlay) {
        exportBtn.addEventListener("click", function() {
            exportOverlay.classList.remove("d-none");
        });
        exportClose.addEventListener("click", function() {
            exportOverlay.classList.add("d-none");
        });
        exportTextarea.addEventListener("click", function() {
            this.select();
        });
    }

    // Import overlay
    var importBtn = document.getElementById("import-btn");
    var importOverlay = document.getElementById("import-overlay");
    var importCancel = document.getElementById("import-cancel");

    if (importBtn && importOverlay) {
        importBtn.addEventListener("click", function() {
            importOverlay.classList.remove("d-none");
        });
        importCancel.addEventListener("click", function() {
            importOverlay.classList.add("d-none");
        });
    }

    // Delete confirmation overlay
    var deleteOverlay = document.getElementById("delete-overlay");
    var deleteCancel = document.getElementById("delete-cancel");
    var deleteConfirm = document.getElementById("delete-confirm");
    var pendingDeleteForm = null;

    document.querySelectorAll("form[action*='/delete']").forEach(function(form) {
        form.addEventListener("submit", function(e) {
            e.preventDefault();
            pendingDeleteForm = this;
            deleteOverlay.querySelector(".overlay-rule-name").textContent = this.dataset.ruleName;
            deleteOverlay.classList.remove("d-none");
        });
    });

    if (deleteCancel) {
        deleteCancel.addEventListener("click", function() {
            deleteOverlay.classList.add("d-none");
            pendingDeleteForm = null;
        });
    }

    if (deleteConfirm) {
        deleteConfirm.addEventListener("click", function() {
            if (pendingDeleteForm) {
                pendingDeleteForm.submit();
            }
        });
    }

    // Run confirmation overlay
    var runOverlay = document.getElementById("run-overlay");
    var runCancel = document.getElementById("run-cancel");
    var runConfirm = document.getElementById("run-confirm");
    var runProgressOverlay = document.getElementById("run-progress-overlay");
    var pendingRunForm = null;

    document.querySelectorAll("form[action*='/run']").forEach(function(form) {
        form.addEventListener("submit", function(e) {
            e.preventDefault();
            pendingRunForm = this;
            runOverlay.querySelector(".overlay-rule-name").textContent = this.dataset.ruleName;
            runOverlay.classList.remove("d-none");
        });
    });

    if (runCancel) {
        runCancel.addEventListener("click", function() {
            runOverlay.classList.add("d-none");
            pendingRunForm = null;
        });
    }

    if (runConfirm) {
        runConfirm.addEventListener("click", function() {
            if (pendingRunForm) {
                runOverlay.classList.add("d-none");
                runProgressOverlay.classList.remove("d-none");
                pendingRunForm.submit();
            }
        });
    }

    // Rule search
    var rulesSearch = document.getElementById("rules-search");
    var rulesNoResults = document.getElementById("rules-no-results");

    if (rulesSearch) {
        rulesSearch.addEventListener("input", function() {
            var query = this.value.trim();
            var cards = document.querySelectorAll(".card[data-rule-name]");
            var visible = 0;

            cards.forEach(function(card) {
                var name = card.dataset.ruleName;
                var show = query === "" || _fuzzyMatch(query, name);
                card.style.display = show ? "" : "none";
                if (show) visible++;
            });

            if (rulesNoResults) {
                rulesNoResults.classList.toggle("d-none", query === "" || visible > 0);
            }
        });
    }
});
