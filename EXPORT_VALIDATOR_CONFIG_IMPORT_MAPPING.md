# Export Validator Config Import Mapping

Source folder reviewed: `C:\Users\jderifield\Documents\RU-STP Validation 2.17.2026`

## Current folder-to-type mapping used by the app

| Folder / Name Pattern | App Item Type Key | Rule Name |
| --- | --- | --- |
| `Agreement Choices` / `AgrChoice*` | `agreement_choices` | `Agreement Choices` |
| `DQT` / `AutomatedTest*` / `*AutoTest*` | `data_quality_tests` | `DQT` |
| `Extended Distribution Staging Tables` / `ExDistStagingTables*` / `ExtendedDistributionStagingTable*` | `extended_distribution_staging_tables` | `Extended Distribution Staging Tables` |
| `HTTP Endpoints` / `HttpEndpoint*` | `http_endpoints` | `HTTP Endpoints` |
| `Item Choices` / `ItemChoice*` | `item_choices` | `Item Choices` |
| `Jobstreams` / `Jobstream*` | `jobstreams` | `Jobstreams` |
| `Promotion` | `promotions` | `Promotion` |
| `Promotion Offers` | `promotion_offers` | `Promotion Offers` |
| `Scripts` / `*_Scripts*` | `scripts` | `Scripts` |
| `Select Sets` / `SelectSet*` | `select_sets` | `Select Sets` |
| `Subscription Choices` / `SubChoice*` | `subscription_choices` | `Subscription Choices` |
| `System Option Values` / `SysOptVal*` | `system_option_values` | `System Option Values` |
| `Workflow Rules` / `WorkflowRule*` / `Workflow*` | `workflow_rules` | `Workflow Rules` |

## Not mapped right now

- `File Transfer Site Codes (NOT SET YET)` is intentionally not mapped because no active export rule/type is configured in `assistant_app/export_rules.json`.

## Import behavior

- Folder import scans recursively for `.xml` and `.csv`.
- Each file is mapped to an item type from folder/file naming patterns above.
- File type is checked against the mapped rule (`xml` vs `csv`).
- XML files are parsed for validity before saving.
- Saved config name is the file path relative to the selected folder (for uniqueness).
- Multiple config files can now be stored under the same item type for one instance.

