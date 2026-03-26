## Fixed

- Removed `delete` and `spam` from the action type dropdown in the rule form. Both were removed from `valid_actions` in 1.0.12 but left in the template, causing "Rule is invalid" if either was selected. (#48)
