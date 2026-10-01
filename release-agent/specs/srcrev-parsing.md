# Parsing generic-srcrev.inc

## Purpose
This file explains how to extract the component list from the `generic-srcrev.inc` file in `meta-rdk-broadband`.

## File Location
`conf/include/generic-srcrev.inc`

## File Format
The file contains lines like these:

```
# Update SRCREV_pn here
SRCREV_pn-ccsp-misc = "abc1234def5678..."
SRCREV_format_pn-ccsp-misc = "1"
SRCREV_pn-utopia = "zzz9999..."
SRCREV_pn-ccsp-psm = "bbb2222..."
SRCREV_scope_branch_pn-utopia = "develop"
```

## Parsing Rules

### Step 1 — Find the start marker
Scan lines until you find a line containing:
```
# Update SRCREV_pn here
```
Begin extracting components from the NEXT line after this marker.

### Step 2 — Extract component name
For each line after the marker, match this pattern:
```
SRCREV(_[\w]+)?_pn-<component_name> = "<sha>"
```

- `component_name` = everything between `_pn-` and the space before `=`

Examples:
```
SRCREV_pn-ccsp-misc = "abc123"       → component: ccsp-misc
```

### Step 3 — Skip non-SHA lines
Skip a line if the value:
- Is shorter than 7 characters
- Is a branch name (contains letters that form a word like "develop", "main", "master")
- Is a single digit ("1", "0")
- Is empty

### Step 4 — Skip comment and blank lines
Skip lines that:
- Start with `#`
- Are empty or whitespace only

Do NOT stop when you encounter blank or comment lines — simply skip them and continue reading.

### Step 5 — Stop condition
Parse until the **end of file**. Never stop early because of blank lines, comment lines, or section separators.
The real file has blank lines separating groups of components — you must read past them.

## Result
A deduplicated list of component names (dictionary keyed by component name, since the same component
may appear in multiple `SRCREV_pn-`, `SRCREV_format_pn-`, `SRCREV_scope_branch_pn-` lines):
```
{ "ccsp-misc", "utopia", "ccsp-psm" }
```

## Finding the GitHub URL for a Component

Once you have the component list, for each component:

1. Search for a `.bb` recipe file matching `<component>.bb` anywhere under the cloned repo
2. If exact match not found, search for `<component>*.bb`
3. Open the recipe file and find the `SRC_URI` line
4. The SRC_URI line may span multiple lines joined by `\` — join them first
5. Extract the GitHub repo name from the pattern: `/<repo-name>;`
6. GitHub URL = `https://github.com/rdkcentral/<repo-name>`

Example SRC_URI:
```
SRC_URI = "git://github.com/rdkcentral/ccsp-misc;branch=develop;..."
```
Extracted: `ccsp-misc` → `https://github.com/rdkcentral/ccsp-misc`

## Special Cases

- If no `.bb` file found for a component → set `github_url = None` for that component.
  The component still stays in the list and will appear in the sheet with NA values.
  Do NOT drop or skip it entirely.
- If SRC_URI does not contain a GitHub rdkcentral URL → set `github_url = None` for that component.
  Same rule: keep it in the list, it will appear in the sheet as NA.
- Components in `additional_components` list in config.yaml already have their GitHub URL provided — use that directly, no recipe lookup needed.

## GitHub repo name vs component name

The repo name extracted from `SRC_URI` is often **different** from the component name in srcrev.inc.
For example:
- srcrev.inc component: `hal-bridgeutil` → recipe SRC_URI contains: `.../rdkcentral/hardware-abstraction-layer;...`
- So `github_url = https://github.com/rdkcentral/hardware-abstraction-layer`

This is expected. Do NOT hardcode these mappings — always read the SRC_URI from the recipe file.
Many hal-* components (hal-cm-generic, hal-wifi-generic, hal-moca-generic, etc.) may all share
the same `hardware-abstraction-layer` GitHub URL. That is correct — still track each component separately
with its own rows in the sheet.
