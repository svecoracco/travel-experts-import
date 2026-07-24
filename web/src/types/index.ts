export interface User {
  id: number;
  email: string;
  display_name: string | null;
  role: "admin" | "operator";
  is_active: boolean;
  company_ids: number[];
  created_at: string;
  updated_at: string;
  last_login_at: string | null;
}

export interface ImportJob {
  id: number;
  plugin_name: string;
  company_id: number;
  status: "pending" | "running" | "completed" | "failed";
  file_name: string;
  dry_run: boolean;
  accounting_date: string | null;
  result_summary: string | null;
  skip_report_path: string | null;
  created_by: number;
  creator_name: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface Company {
  company_id: number;
  name: string;
  scripts?: string[];
}

export interface PluginMeta {
  name: string;
  display_name: string;
  accepted_extensions: string[];
  description: string;
}

export interface ExecutionResult {
  created: number;
  skipped: number;
  errors: number;
  items_processed: number;
  log_messages: string[];
}

export interface ImportRunResponse {
  job: ImportJob;
  result: ExecutionResult | { error: string; validation_errors?: string[] };
}

export interface AppConfigEntry {
  id: number;
  company_id: number;
  script_name: string;
  key: string;
  value: any;
  updated_at: string | null;
  updated_by: number | null;
}
