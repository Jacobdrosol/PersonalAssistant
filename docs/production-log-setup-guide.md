# Production Log Automation Setup Guide

This guide explains how to configure a reusable production-log workflow that reads CSV data, routes each CSV row to the correct worksheet, writes only to approved empty cells, preserves the workbook, and runs manually or on a schedule.

## Safety guarantees

- Existing cell values are never overwritten.
- When a row requires several destination cells, **all required cells must be empty**. Otherwise, the entire source row is skipped and reported.
- Date-matched updates use the matching date row only. They do not fall through to a different empty row.
- The date-matched writer changes only the approved cell values and Excel recalculation settings. Formatting, formulas, borders, colors, alignments, drawings, tables, comments, validations, and unrelated workbook content remain unchanged.
- A recoverable workbook backup is created before a production update.
- Email categories and read status change only after the workbook update succeeds.
- Temporary email attachments are removed after their bytes are read.

## How the pieces fit together

1. A **client** identifies one production workbook.
2. Each worksheet has its own **sheet mapping**.
3. An **automation** defines where the CSV comes from, how rows are routed, and when the workflow runs.
4. The routing CSV column selects a worksheet by comparing its value with that worksheet's route values.
5. The selected worksheet's mapping determines which CSV headers write to which workbook columns.

Mappings are independent. A client may have 26 worksheets with completely different route values, CSV headers, destination columns, header rows, and data-start rows.

## 1. Create the client and select its workbook

1. Select **New...** under **Client Setup**.
2. Enter a recognizable client name.
3. Choose **Select Workbook...** and select the `.xlsx` or `.xlsm` production log.
4. Select a worksheet from the **Sheet** list.

Keep the production workbook closed while an update runs. The preview is read-only.

## 2. Configure each worksheet

Choose **Configure Sheet Mappings...** under **Scheduled Automations** to open the dedicated mapping manager. It lists every workbook sheet together with its configuration status, route count, mapping count, and saved row positions.

Repeat this section for every worksheet the automation may update.

### Header row and data-start row

- **Header row** is the workbook row containing the worksheet's column headings.
- **Data starts row** is the first row the importer may examine or update.
- Choose **Reload Columns** after changing these values so the preview and destination-column list use the new layout.

These values are saved separately for every worksheet.

### Sheet type

Choose a predefined sheet type when its named fields match the worksheet. Choose **Custom / Generic** when the worksheet uses different fields or an unfamiliar layout.

For Custom / Generic mappings, assign a CSV header only to the workbook columns that should be updated. Unmapped columns are ignored.

### CSV header and spreadsheet column

Each mapping row connects:

| Setting | Meaning |
|---|---|
| Field | A friendly name used to keep the mapping understandable |
| CSV header | The exact header from row 1 of the incoming CSV |
| Spreadsheet column | The destination workbook column for this worksheet |

The same CSV header may be sent to different destination columns on different worksheets. One worksheet might map `Record Count` to column `C`, while another maps it to `G`.

### CSV route values

Route values answer: **When the routing column contains this value, should this CSV row go to the selected worksheet?**

- Enter one or more values separated by commas, semicolons, or line breaks.
- Leave the box blank to use the worksheet name as its route value.
- Route values are compared without capitalization, spaces, punctuation, hyphens, or underscores affecting the match.
- A route value may identify only one worksheet within a client.

Example:

```text
Routing CSV column: Select Set Name

Worksheet: Single - Bill Mailed
CSV route value: NUB_Single_Bills_DRY_Mailed
```

Every CSV row whose `Select Set Name` is `NUB_Single_Bills_DRY_Mailed` uses the mapping saved for `Single - Bill Mailed`.

Choose **Save Selected Sheet** before selecting the next worksheet. The smaller mapping editor on the main tab edits the same stored settings, but the dedicated manager is recommended for clients with many worksheets.

## 3. Load a sample CSV

Choose **Load Sample CSV...** and select a representative file.

- Row 1 must contain unique column headers.
- Remaining rows contain data.
- Loading a sample populates the CSV-header choices in the sheet-mapping editor.
- The sample is not imported until **Import Sample Now** is selected.

Configure and save every worksheet mapping before running a sample import.

## 4. Configure the automation

### Email source

| Setting | Meaning |
|---|---|
| Outlook folder | Full path such as `Mailbox Name/Inbox/Production` |
| Outlook access | `classic_outlook`, `new_outlook`, or `auto` |
| Email subject | Text the subject must contain |
| Exact subject | Requires the entire subject to match |
| Sender | Text that must appear in the sender address |
| Body contains | Text that must appear in the message body |
| Attachment | Filename pattern such as `*.csv` |
| Required category | Only messages with this category are pending |
| Completed category | Category applied after successful processing |

Use a required category when the folder may contain unrelated messages. It also provides a durable backlog: messages remain pending across weekends, vacations, or powered-off computers.

#### Classic Outlook

`classic_outlook` is the preferred local source when available. The automation connects to Classic Outlook, starts its configured send/receive groups, waits for mailbox synchronization, and then scans the folder. Users do not need to open the target folder manually. Classic Outlook must be signed in and online.

#### New Outlook

`new_outlook` uses Windows accessibility because New Outlook does not expose the Classic COM interface. New Outlook must be running in an unlocked Windows session. Some controls require trusted pointer clicks, so the app displays a warning before temporarily foregrounding Outlook and moving the pointer.

`auto` uses New Outlook when its window is available and otherwise attempts Classic Outlook. Choose an explicit source when predictable behavior matters.

### Routing and update mode

| Setting | Meaning |
|---|---|
| Routing CSV column | CSV column whose value selects the worksheet mapping |
| Update mode | `append_empty` or `match_date` |
| Sort CSV by | CSV column used to process rows in chronological order |
| CSV date | CSV column containing the run date/time |
| Workbook date column | Worksheet column containing the corresponding date |

#### `append_empty`

Finds the next row where every mapped destination cell is empty.

#### `match_date`

Parses the CSV date, finds that date in the configured workbook date column, and updates only that row. If any mapped destination cell already contains data, the source row is skipped and reported.

## 5. Configure the schedule

| Setting | Meaning |
|---|---|
| Time | Local scheduled time in `HH:MM` format |
| Run on | Weekdays on which this automation has a scheduled occurrence |
| Catch up after missed time | Runs after startup when the scheduled occurrence was missed |
| Prior days to include | Search window used when no required category is configured |
| Retry every | Delay before retrying a no-data or failed occurrence |
| Paused | Prevents scheduled execution without deleting the setup |

When a required category is configured, every matching categorized message is processed oldest first regardless of age. This handles weekends and other missed days without creating separate weekend rules.

Choose **Save Automation** after making changes.

## 6. Test before relying on the schedule

1. Close the production workbook.
2. Load a representative sample CSV.
3. Confirm every worksheet's route values and mappings.
4. Use **Import Sample Now** only against a copied/non-production workbook first.
5. Review the updated values, formulas, and formatting.
6. Restore the production workbook path.
7. Use **Run Automation Now** for one controlled live test.
8. Confirm the workbook, email category, read status, and run result.

## Skips and exception reports

A source row is skipped when, for example:

- no worksheet route matches;
- a required CSV value is blank;
- the source date cannot be parsed;
- no workbook row matches the source date; or
- one or more destination cells are already occupied.

Exception reports include the source row, target worksheet and row, attempted values, and existing workbook values. Reports are stored under the Personal Assistant user-data directory in `production_log_reports`.

## Recommended setup pattern for large clients

For a client with many worksheets:

1. Build one representative sample CSV containing every expected route and source header.
2. Select the first worksheet.
3. Set its header/data rows, sheet type, route values, and mappings.
4. Save the mapping.
5. Repeat for each worksheet.
6. Verify that no route value is assigned to two worksheets.
7. Configure one or more automations after all sheet mappings are saved.

The number of worksheets does not require shared columns or shared field layouts. Each selected worksheet is its own routing and mapping rule.
