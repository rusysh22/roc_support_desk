import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'roc_desk.settings')
django.setup()
from django.db import connection

sql = """
ALTER TABLE cases_casecategory ADD COLUMN IF NOT EXISTS is_need_admin_approval boolean DEFAULT false NOT NULL;
ALTER TABLE cases_casecategory ALTER COLUMN is_need_admin_approval DROP DEFAULT;
ALTER TABLE cases_casecategory ADD COLUMN IF NOT EXISTS is_use_routing_approval boolean DEFAULT false NOT NULL;
ALTER TABLE cases_casecategory ALTER COLUMN is_use_routing_approval DROP DEFAULT;
CREATE TABLE IF NOT EXISTS cases_ticketapproval (id uuid NOT NULL PRIMARY KEY, created_at timestamp with time zone NOT NULL, updated_at timestamp with time zone NOT NULL, tier smallint NOT NULL CHECK (tier >= 0), status varchar(20) NOT NULL, comments text NOT NULL, actioned_at timestamp with time zone NULL, approver_id uuid NULL, case_id uuid NOT NULL, created_by_id bigint NULL, updated_by_id bigint NULL);
ALTER TABLE cases_ticketapproval DROP CONSTRAINT IF EXISTS cases_ticketapproval_approver_id_6bcdecac_fk_core_employee_id;
ALTER TABLE cases_ticketapproval ADD CONSTRAINT cases_ticketapproval_approver_id_6bcdecac_fk_core_employee_id FOREIGN KEY (approver_id) REFERENCES core_employee (id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE cases_ticketapproval DROP CONSTRAINT IF EXISTS cases_ticketapproval_case_id_dc6db3a2_fk_cases_caserecord_id;
ALTER TABLE cases_ticketapproval ADD CONSTRAINT cases_ticketapproval_case_id_dc6db3a2_fk_cases_caserecord_id FOREIGN KEY (case_id) REFERENCES cases_caserecord (id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE cases_ticketapproval DROP CONSTRAINT IF EXISTS cases_ticketapproval_created_by_id_de0f92df_fk_core_user_id;
ALTER TABLE cases_ticketapproval ADD CONSTRAINT cases_ticketapproval_created_by_id_de0f92df_fk_core_user_id FOREIGN KEY (created_by_id) REFERENCES core_user (id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE cases_ticketapproval DROP CONSTRAINT IF EXISTS cases_ticketapproval_updated_by_id_ecb917c8_fk_core_user_id;
ALTER TABLE cases_ticketapproval ADD CONSTRAINT cases_ticketapproval_updated_by_id_ecb917c8_fk_core_user_id FOREIGN KEY (updated_by_id) REFERENCES core_user (id) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX IF NOT EXISTS cases_ticketapproval_approver_id_6bcdecac ON cases_ticketapproval (approver_id);
CREATE INDEX IF NOT EXISTS cases_ticketapproval_case_id_dc6db3a2 ON cases_ticketapproval (case_id);
CREATE INDEX IF NOT EXISTS cases_ticketapproval_created_by_id_de0f92df ON cases_ticketapproval (created_by_id);
CREATE INDEX IF NOT EXISTS cases_ticketapproval_updated_by_id_ecb917c8 ON cases_ticketapproval (updated_by_id);
"""

with connection.cursor() as cursor:
    cursor.execute(sql)
print("SQL Applied")
