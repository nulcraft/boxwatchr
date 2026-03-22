var TEXT_OPERATORS_HTML = '<option value="equals">equals</option><option value="contains">contains</option><option value="is_empty">is empty</option>';
var NUMERIC_OPERATORS_HTML = '<option value="greater_than">greater than</option><option value="less_than">less than</option><option value="greater_than_or_equal">greater than or equal</option><option value="less_than_or_equal">less than or equal</option>';

function onFieldChange(select) {
    var row = select.closest(".condition-row");
    var operatorSelect = row.querySelector(".cond-operator");
    var textInput = row.querySelector(".value-text");
    var boolSelect = row.querySelector(".value-bool");
    var isNumeric = select.value === "rspamd_score";

    operatorSelect.innerHTML = isNumeric ? NUMERIC_OPERATORS_HTML : TEXT_OPERATORS_HTML;

    if (isNumeric) {
        textInput.disabled = false;
        textInput.classList.remove("d-none");
        boolSelect.disabled = true;
        boolSelect.classList.add("d-none");
    } else {
        onOperatorChange(operatorSelect);
    }
}

function onOperatorChange(select) {
    var row = select.closest(".condition-row");
    var textInput = row.querySelector(".value-text");
    var boolSelect = row.querySelector(".value-bool");
    var isEmpty = select.value === "is_empty";
    textInput.disabled = isEmpty;
    textInput.classList.toggle("d-none", isEmpty);
    boolSelect.disabled = !isEmpty;
    boolSelect.classList.toggle("d-none", !isEmpty);
}

function onActionTypeChange(select) {
    var row = select.closest(".action-row");
    var destInput = row.querySelector(".action-dest");
    var isMove = select.value === "move";
    destInput.classList.toggle("d-none", !isMove);
    destInput.disabled = !isMove;
}

function removeCondition(btn) {
    btn.closest(".condition-row").remove();
    updateEmptyNotice("conditions-container", "conditions-empty");
}

function removeAction(btn) {
    btn.closest(".action-row").remove();
    updateEmptyNotice("actions-container", "actions-empty");
}

function updateEmptyNotice(containerId, noticeId) {
    var container = document.getElementById(containerId);
    var notice = document.getElementById(noticeId);
    if (!notice) return;
    notice.classList.toggle("d-none", container.children.length > 0);
}

function addCondition() {
    var template = document.getElementById("condition-template");
    var clone = template.content.cloneNode(true);
    document.getElementById("conditions-container").appendChild(clone);
    var notice = document.getElementById("conditions-empty");
    if (notice) notice.classList.add("d-none");
}

function prepareSubmit() {
    document.querySelectorAll(".action-dest").forEach(function (el) {
        el.disabled = false;
    });
}

function addAction() {
    var template = document.getElementById("action-template");
    var clone = template.content.cloneNode(true);
    var typeSelect = clone.querySelector("[name='action_type']");
    onActionTypeChange(typeSelect);
    document.getElementById("actions-container").appendChild(clone);
    var notice = document.getElementById("actions-empty");
    if (notice) notice.classList.add("d-none");
}
