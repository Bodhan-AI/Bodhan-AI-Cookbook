export interface User {
  id: number;
  full_name: string;
  email: string;
  phone?: string;
  role: 'farmer' | 'officer' | 'admin';
  state?: string;
  district?: string;
  preferred_language: string;
  created_at: string;
}

export interface InvestigationNote {
  id: number;
  report_id: string;
  officer_id?: number;
  officer_name: string;
  note: string;
  created_at: string;
}

export interface Report {
  id: string;
  farmer_id: number;
  farmer_name?: string;
  crop_name: string;
  crop_variety?: string;
  growth_stage?: string;
  symptoms: string;
  custom_symptom?: string;
  source_language: string;
  original_transcription: string;
  english_translation: string;
  audio_file_path?: string;
  submission_method: 'voice' | 'text';
  state: string;
  district: string;
  village: string;
  farm_location?: string;
  latitude?: number;
  longitude?: number;
  observation_date: string;
  affected_plants?: number;
  investigation_status: 'Submitted' | 'Under Review' | 'Investigation' | 'Resolved' | 'Closed';
  created_at: string;
  updated_at: string;
  notes: InvestigationNote[];
}

export interface Alert {
  id: string;
  crop_name: string;
  state: string;
  district: string;
  symptoms: string;
  matching_report_count: number;
  severity: 'Low' | 'Medium' | 'High';
  detection_period: string;
  status: 'Active' | 'Investigating' | 'Resolved' | 'Dismissed';
  disclaimer: string;
  created_at: string;
}

export interface NotificationItem {
  id: number;
  user_id: number;
  title: string;
  message: string;
  type: 'report' | 'alert' | 'system';
  link?: string;
  is_read: boolean;
  created_at: string;
}

export interface AnalyticsOverview {
  total_reports: number;
  reports_today: number;
  reports_under_investigation: number;
  reports_resolved: number;
  active_alerts: number;
  total_farmers: number;
}
