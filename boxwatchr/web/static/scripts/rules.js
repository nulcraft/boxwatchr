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
});
