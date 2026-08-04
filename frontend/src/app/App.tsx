import { useCallback, useEffect, useState } from "react";

import { getCurrentUser, logout, type AuthUser } from "../api/auth";
import type { ApiError } from "../api/client";
import { LoginPage } from "../features/auth/LoginPage";
import { CampaignManagementPage } from "../features/campaign/CampaignManagementPage";
import { DepartmentDashboardPage } from "../features/department/DepartmentDashboardPage";
import { DashboardPage } from "../features/dashboard/DashboardPage";
import { AppShell, type ShellTab } from "./AppShell";

type AuthState =
  | { status: "checking" }
  | { status: "unauthenticated" }
  | { status: "authenticated"; user: AuthUser }
  | { status: "error"; message: string };

type TabKey = "dashboard" | "campaigns" | "insights";

const roleLabels: Record<AuthUser["role"], string> = {
  admin: "관리자",
  analyst: "분석 담당자",
  operations: "운영 담당자",
  marketing: "마케팅 담당자",
};

const roleDescriptions: Record<AuthUser["role"], string> = {
  admin: "팀 계정과 업무 권한을 관리합니다.",
  analyst: "고객 분석 결과와 모델 배치 상태를 확인합니다.",
  operations: "고위험 고객의 상담과 후속 처리를 관리합니다.",
  marketing: "고객 세그먼트와 캠페인 실행 결과를 관리합니다.",
};

function tabsForUser(user: AuthUser): ShellTab<TabKey>[] {
  if (user.role === "admin") {
    return [
      { key: "dashboard", label: "관리자 콘솔" },
      { key: "campaigns", label: "캠페인 관리" },
      { key: "insights", label: "고객 분석 대시보드" },
    ];
  }
  if (user.role === "operations") {
    return [
      { key: "dashboard", label: "운영 업무 센터" },
      { key: "campaigns", label: "캠페인 관리" },
    ];
  }
  if (user.role === "analyst") {
    return [
      { key: "insights", label: "고객 분석 대시보드" },
      { key: "campaigns", label: "캠페인 조회" },
    ];
  }
  return [{ key: "campaigns", label: "마케팅 캠페인 센터" }];
}

function defaultTabForUser(user: AuthUser): TabKey {
  if (user.role === "analyst") {
    return "insights";
  }
  if (user.role === "marketing") {
    return "campaigns";
  }
  return "dashboard";
}

function shellContentForTab(user: AuthUser, tab: TabKey): { eyebrow: string; title: string; subtitle: string } {
  if (tab === "insights") {
    return {
      eyebrow: "CARDOPS CONSOLE / INSIGHTS",
      title: "고객 분석 대시보드",
      subtitle: "고객 행동 데이터를 기반으로 이탈 위험과 실행 우선순위를 확인합니다.",
    };
  }
  if (tab === "campaigns") {
    if (user.role === "analyst") {
      return {
        eyebrow: "CARDOPS CONSOLE / CAMPAIGNS",
        title: "캠페인 조회",
        subtitle: "캠페인 현황과 처리 이력을 조회합니다. 변경 작업은 담당 부서에서 수행합니다.",
      };
    }
    if (user.role === "marketing") {
      return {
        eyebrow: "CARDOPS CONSOLE / CAMPAIGNS",
        title: "마케팅 캠페인 센터",
        subtitle: "후보 고객 선정부터 캠페인 생성·타기팅·성과 확인까지 한 곳에서 관리합니다.",
      };
    }
    return {
      eyebrow: "CARDOPS CONSOLE / CAMPAIGNS",
      title: "캠페인 관리",
      subtitle: "캠페인 실행 기간, 대상 고객, 처리 결과와 변경 이력을 한 곳에서 관리합니다.",
    };
  }
  return {
    eyebrow: `CARDOPS CONSOLE / ${roleLabels[user.role].toUpperCase()}`,
    title: user.role === "operations" ? "운영 업무 센터" : "관리자 콘솔",
    subtitle: roleDescriptions[user.role],
  };
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "로그인 상태를 확인하지 못했습니다.";
}

function isUnauthorized(error: unknown): boolean {
  return (error as ApiError | undefined)?.status === 401;
}

function AuthLoading() {
  return (
    <main className="auth-state auth-state--loading" aria-busy="true">
      <div className="auth-state__card">
        <span className="auth-state__mark" aria-hidden="true" />
        <p>로그인 상태를 확인하고 있습니다.</p>
      </div>
    </main>
  );
}

function AuthError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <main className="auth-state">
      <div className="auth-state__card">
        <span className="auth-state__mark auth-state__mark--error" aria-hidden="true" />
        <h1>서비스에 연결할 수 없습니다.</h1>
        <p>{message}</p>
        <button className="auth-state__button" type="button" onClick={onRetry}>
          다시 시도
        </button>
      </div>
    </main>
  );
}

export function App() {
  const [authState, setAuthState] = useState<AuthState>({
    status: "checking",
  });
  const [activeTab, setActiveTab] = useState<TabKey>("dashboard");
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");

  useEffect(() => {
    let isActive = true;

    const checkSession = async () => {
      try {
        const user = await getCurrentUser();
        if (isActive) {
          setActiveTab(defaultTabForUser(user));
          setAuthState({ status: "authenticated", user });
        }
      } catch (error) {
        if (!isActive) {
          return;
        }

        if (isUnauthorized(error)) {
          setAuthState({ status: "unauthenticated" });
          return;
        }

        setAuthState({ status: "error", message: getErrorMessage(error) });
      }
    };

    void checkSession();
    return () => {
      isActive = false;
    };
  }, []);

  const retrySession = useCallback(() => {
    setAuthState({ status: "checking" });
    void getCurrentUser()
      .then((user) => {
        setActiveTab(defaultTabForUser(user));
        setAuthState({ status: "authenticated", user });
      })
      .catch((error: unknown) => {
        if (isUnauthorized(error)) {
          setAuthState({ status: "unauthenticated" });
          return;
        }

        setAuthState({ status: "error", message: getErrorMessage(error) });
      });
  }, []);

  if (authState.status === "checking") {
    return <AuthLoading />;
  }

  if (authState.status === "error") {
    return <AuthError message={authState.message} onRetry={retrySession} />;
  }

  if (authState.status === "unauthenticated") {
    return (
      <LoginPage
        onAuthenticated={(user) => {
          setActiveTab(defaultTabForUser(user));
          setAuthState({ status: "authenticated", user });
        }}
      />
    );
  }

  const handleLogout = async () => {
    setIsLoggingOut(true);
    setLogoutError("");
    try {
      await logout();
      setAuthState({ status: "unauthenticated" });
    } catch (error) {
      setLogoutError(error instanceof Error ? error.message : "로그아웃하지 못했습니다.");
    } finally {
      setIsLoggingOut(false);
    }
  };

  const { user } = authState;
  const { eyebrow, title, subtitle } = shellContentForTab(user, activeTab);

  return (
    <AppShell
      user={user}
      roleLabel={roleLabels[user.role]}
      eyebrow={eyebrow}
      title={title}
      subtitle={subtitle}
      tabs={tabsForUser(user)}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      onLogout={() => void handleLogout()}
      isLoggingOut={isLoggingOut}
      logoutError={logoutError}
    >
      {activeTab === "dashboard" && <DepartmentDashboardPage user={user} />}
      {activeTab === "campaigns" && <CampaignManagementPage user={user} />}
      {activeTab === "insights" && (
        <DashboardPage user={user} showCampaignFeedback={user.role === "admin"} />
      )}
    </AppShell>
  );
}
