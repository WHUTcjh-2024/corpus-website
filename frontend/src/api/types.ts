export type AccessScope = "none" | "demo_only" | "standard" | "admin";

export type SessionPayload = {
  is_authenticated: boolean;
  access_scope: AccessScope;
  user: null | {
    id: number;
    username: string;
    email: string;
    is_staff: boolean;
    is_superuser: boolean;
    display_name: string;
  };
  profile: null | {
    full_name: string;
    organization: string;
    email: string;
    role: string;
    role_label: string;
    status: string;
    status_label: string;
  };
};
