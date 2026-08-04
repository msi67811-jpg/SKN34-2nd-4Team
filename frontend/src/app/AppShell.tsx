import type { ReactNode } from "react";

import type { AuthUser } from "../api/auth";

export type ShellTab<TKey extends string> = {
  key: TKey;
  label: string;
};

type AppShellProps<TKey extends string> = {
  user: AuthUser;
  roleLabel: string;
  eyebrow: string;
  title: string;
  subtitle: string;
  tabs: ShellTab<TKey>[];
  activeTab: TKey;
  onTabChange: (key: TKey) => void;
  onLogout: () => void;
  isLoggingOut: boolean;
  logoutError: string;
  children: ReactNode;
};

export function AppShell<TKey extends string>({
  user,
  roleLabel,
  eyebrow,
  title,
  subtitle,
  tabs,
  activeTab,
  onTabChange,
  onLogout,
  isLoggingOut,
  logoutError,
  children,
}: AppShellProps<TKey>) {
  return (
    <main className="dashboard-layout">
      <header className="dashboard-header">
        <div className="dashboard-brand-block">
          <p className="dashboard-eyebrow">{eyebrow}</p>
          <h1>{title}</h1>
          <p className="dashboard-subtitle">{subtitle}</p>
        </div>
        <div className="dashboard-account">
          <div>
            <strong>{user.display_name}</strong>
            <span>{roleLabel}</span>
          </div>
          {tabs.length > 1 && tabs.map((tab) => (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.key}
              className={`dashboard-nav-button${activeTab === tab.key ? " is-active" : ""}`}
              onClick={() => onTabChange(tab.key)}
            >
              {tab.label}
            </button>
          ))}
          <button
            className="dashboard-logout"
            type="button"
            onClick={onLogout}
            disabled={isLoggingOut}
          >
            {isLoggingOut ? "처리 중..." : "로그아웃"}
          </button>
        </div>
      </header>
      {logoutError !== "" && <p className="dashboard-notice" role="alert">{logoutError}</p>}
      {children}
    </main>
  );
}
