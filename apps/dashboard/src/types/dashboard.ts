// Mirrors haven/mcp/dashboard.py (DashboardSnapshot and the models it
// contains). That module is the source of truth: when it changes, update
// this file to match. Optional Python fields arrive as `null`, never absent.

export type StatusTone = "ok" | "warn" | "bad" | "neutral";

export interface PersonSummary {
  id: string;
  name: string;
  home: boolean;
}

export interface ContextDevice {
  id: string;
  name: string;
  room_id: string | null;
  capabilities: string[];
  available: boolean;
}

export interface MediaOption {
  id: string;
  title: string;
  duration_minutes: number;
  available: boolean;
}

export interface HouseholdContext {
  household_id: string;
  people: PersonSummary[];
  rooms: { id: string; name: string }[];
  devices: ContextDevice[];
  media_options: MediaOption[];
  available_minutes: number | null;
  preferred_room_id: string | null;
  preferred_genres: string[];
}

export interface DashboardRoom {
  id: string;
  name: string;
  occupied: boolean;
  current_activity: string | null;
}

export interface DashboardDevice {
  id: string;
  name: string;
  device_type: string;
  room_id: string | null;
  online: boolean;
  state: Record<string, unknown>;
}

export interface DashboardLock {
  device_id: string;
  name: string;
  locked: boolean;
  online: boolean;
}

export interface DashboardAlarm {
  device_id: string;
  name: string;
  armed: boolean;
  mode: string;
  online: boolean;
}

export interface DashboardSecurity {
  armed: boolean;
  all_locked: boolean;
  locks: DashboardLock[];
  alarms: DashboardAlarm[];
}

export interface DashboardMediaSession {
  device_id: string;
  media_id: string;
  title: string;
  status: string;
}

export interface DashboardStep {
  id: string;
  order: number;
  action: string;
  title: string;
  description: string;
  risk: string;
  requires_confirmation: boolean;
  expected_effect: string | null;
  device_id: string | null;
  execution_status: string | null;
  execution_detail: string | null;
  attempts: number | null;
  verification_status: string | null;
  verification_detail: string | null;
}

export interface DashboardPlan {
  plan_id: string;
  goal: string;
  workflow: string;
  status: string;
  summary: string;
  requires_confirmation: boolean;
  clarification_needed: boolean;
  clarification_prompt: string | null;
  rejected_candidate_count: number;
  created_at: string;
  steps: DashboardStep[];
  execution_status: string | null;
  verification_status: string | null;
  rationale: string[];
}

export interface DashboardPlanListItem {
  plan_id: string;
  goal: string;
  plan_status: string;
  execution_status: string | null;
  verification_status: string | null;
  created_at: string;
}

export interface DashboardActivity {
  activity_type: string;
  summary: string;
  occurred_at: string;
}

export interface DashboardSnapshot {
  generated_at: string;
  household_id: string;
  household_name: string;
  planner_backend: string;
  persistence_backend: string;
  context: HouseholdContext;
  rooms: DashboardRoom[];
  devices: DashboardDevice[];
  security: DashboardSecurity;
  media_sessions: DashboardMediaSession[];
  active_plan: DashboardPlan | null;
  recent_plans: DashboardPlanListItem[];
  recent_activity: DashboardActivity[];
}
