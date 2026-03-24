document.addEventListener("DOMContentLoaded", function() {
    document.querySelectorAll("tr[data-email-id]").forEach(function(row) {
        row.addEventListener("click", function() {
            location.href = "/emails/" + this.dataset.emailId;
        });
    });
});
