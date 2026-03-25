(function () {
    "use strict";

    if (sessionStorage.getItem("bw_update_checked")) return;

    fetch("/api/version/check")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
            if (!data) return;
            sessionStorage.setItem("bw_update_checked", "1");
            if (!data.update_available) return;

            var latest = data.latest;
            if (localStorage.getItem("bw_dismissed_version") === latest) return;

            var body = document.getElementById("update-toast-body");
            if (!body) return;

            body.innerHTML =
                "<div>" + latest + " is available. You have " + data.current + ".</div>"
                + "<div class=\"mt-2 d-flex gap-3 align-items-center\">"
                + "<a href=\"https://github.com/nulcraft/boxwatchr/blob/main/CHANGELOG.md\""
                + " target=\"_blank\" rel=\"noopener noreferrer\">See what changed &rarr;</a>"
                + "<button type=\"button\" class=\"btn-link-muted\" id=\"update-dismiss-btn\">Don't show again</button>"
                + "</div>";

            var el = document.getElementById("update-toast");
            if (!el) return;

            document.getElementById("update-dismiss-btn").addEventListener("click", function () {
                localStorage.setItem("bw_dismissed_version", latest);
                bootstrap.Toast.getInstance(el).hide();
            });

            new bootstrap.Toast(el, { autohide: false }).show();
        })
        .catch(function () {});
}());
