📌 How to use the template
For a new script
cp ~/leo-services/scripts/script_template.sh \
   ~/leo-services/scripts/<new_script_name>.sh

nano ~/leo-services/scripts/<new_script_name>.sh


Then update:

File:

Version:

Change:

“Script Purpose” text

logic in main()

Finally:

chmod +x ~/leo-services/scripts/<new_script_name>.sh

For retrofitting an existing script

Add the header + structure at the top

Move logic into main() when practical

Bump version and add a Change: line

This keeps everything formatted consistently across the fleet.

🎯 Why this layout works

Header → traceability + discipline

Purpose block → future context at a glance

Sections → predictable organization

main() entrypoint → safer modifications

set -euo pipefail → reliable execution

Same philosophy as your xmrig launcher — but generalized.
