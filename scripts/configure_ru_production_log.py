from __future__ import annotations

import argparse
from pathlib import Path

from assistant_app.database import Database


WORKBOOK = Path(
    "C:/Users/jderifield/Hearst/Advantage US - Documents/"
    "15 Client Migration Discovery and Project Tracking/The New Republic (RU)/"
    "CORE - The New Republic/Production Log/RU - -LIVE  Production Calendar&Log.xlsx"
)

SHEET_ROUTES = {
    "Single - Bill Mailed": "NUB_Single_Bills_DRY_Mailed",
    "Single - Bill Email": "NUB_Single_Bills_DRY_Emailed",
    "Gift - Bill Mailed": "NUB_Gift_Bills_DRY_Mailed",
    "Single - Renewal Mailed": "NUB_Single_Renewals_DRY_Mailed",
    "Single - Renewal Email": "NUB_Single_Renewals_DRY_Emailed",
    "Auto - Mailed Link": "NUB_Single_Auto_Renewal_Link_Dry_Mailed",
    "Auto - eLink": "NUB_Single_Auto_Renewal_Link_Dry_Emailed",
    "Auto - Apply": "NUB_Single_Auto_Renewal_Dry_Apply",
}


def configure(database_path: Path) -> None:
    if not WORKBOOK.exists():
        raise SystemExit(f"Workbook not found: {WORKBOOK}")
    db = Database(database_path)
    try:
        clients = db.get_production_log_clients()
        client = next((item for item in clients if item.name == "RU-PRD"), None)
        client_id = client.id if client else db.create_production_log_client("RU-PRD")
        db.update_production_log_client_workbook(client_id, str(WORKBOOK))

        for sheet_name, route_value in SHEET_ROUTES.items():
            header_row = 15 if sheet_name == "Single - Renewal Mailed" else 10
            db.upsert_production_log_sheet_config(
                client_id=client_id,
                sheet_name=sheet_name,
                template_key="custom_generic",
                header_row=header_row,
                data_start_row=header_row + 1,
                column_mappings={"id": "B", "count": "C"},
                source_mappings={"id": "Distribution Run ID", "count": "Record Count"},
                route_values=[route_value],
            )

        automation = next(
            (item for item in db.get_production_log_automations(client_id) if item.name == "RU Daily Dry Runs"),
            None,
        )
        automation_id = automation.id if automation else db.create_production_log_automation(client_id, "RU Daily Dry Runs")
        db.update_production_log_automation(
            automation_id,
            name="RU Daily Dry Runs",
            outlook_source="classic_outlook",
            email_folder="Jacob.Derifield@cds-global.com/PRD/Daily Run Val/RU",
            email_subject_contains="RU Daily Runs",
            email_subject_exact=True,
            email_sender_contains="CDSGLOBAL@CDS-GLOBAL.COM",
            email_body_contains="Please validate these selects that ran for RU today.",
            attachment_pattern="*.csv",
            required_category="Not Updated Production Log",
            completed_category="Updated Production Log",
            routing_column="Select Set Name",
            update_mode="match_date",
            source_sort_column="Beginning Date/Time",
            source_date_column="Beginning Date/Time",
            target_date_column="A",
            scheduled_time="08:30",
            weekdays=list(range(7)),
            lookback_days=30,
            catch_up=True,
            retry_minutes=15,
            paused=False,
        )
        # Preserve, but disable, the incomplete draft created during initial setup
        # so it cannot generate a second failed run every morning.
        for draft in db.get_production_log_automations(client_id):
            if draft.id == automation_id or draft.email_folder.strip():
                continue
            db.update_production_log_automation(
                draft.id,
                name=draft.name,
                outlook_source=draft.outlook_source,
                email_folder=draft.email_folder,
                email_subject_contains=draft.email_subject_contains,
                email_subject_exact=draft.email_subject_exact,
                email_sender_contains=draft.email_sender_contains,
                email_body_contains=draft.email_body_contains,
                attachment_pattern=draft.attachment_pattern,
                required_category=draft.required_category,
                completed_category=draft.completed_category,
                routing_column=draft.routing_column,
                update_mode=draft.update_mode,
                source_sort_column=draft.source_sort_column,
                source_date_column=draft.source_date_column,
                target_date_column=draft.target_date_column,
                scheduled_time=draft.scheduled_time,
                weekdays=draft.weekdays,
                lookback_days=draft.lookback_days,
                catch_up=draft.catch_up,
                retry_minutes=draft.retry_minutes,
                paused=True,
            )
        print(f"Configured RU-PRD client {client_id}, automation {automation_id}, and {len(SHEET_ROUTES)} sheet routes.")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    arguments = parser.parse_args()
    configure(arguments.database.resolve())
