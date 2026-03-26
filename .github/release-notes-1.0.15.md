## Fixed

- Removed dead `delete` and `spam` action type branches from `notes.py`. (#50)

- Non-move actions (learn_ham, flag, etc.) no longer disappear and cause "Rule is invalid" when saving. The form parser now iterates action types directly and only pulls a destination for move actions, instead of zipping two parallel lists that came back different lengths due to disabled inputs not being submitted. (#51)
