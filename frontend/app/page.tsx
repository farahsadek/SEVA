"use client";

import { useState, useEffect } from "react";
import WelcomePage from "../components/WelcomePage";
import Onboarding from "../components/Onboarding";
import ChatScreen from "../components/ChatScreen";

export default function Home() {
  const [screen, setScreen]         = useState<"welcome" | "onboarding" | "chat">("welcome");
  const [profile, setProfile]       = useState<any>(null);
  const [isUpdate, setIsUpdate]     = useState(false);
  const [onboardingTab, setOnboardingTab] = useState<"new" | "returning">("new");
  const [ready, setReady]           = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem("seva_profile");
    if (stored) {
      try {
        const parsed = JSON.parse(stored);
        if (parsed.saved_destinations) {
          setProfile(parsed);
          setScreen("chat");
        } else {
          localStorage.removeItem("seva_profile");
        }
      } catch {
        localStorage.removeItem("seva_profile");
      }
    }
    setReady(true);
  }, []);

  function handleProfileReady(p: any) {
    localStorage.setItem("seva_profile", JSON.stringify(p));
    setProfile(p);
    setIsUpdate(false);
    setScreen("chat");
  }

  function handleLogout() {
    localStorage.removeItem("seva_profile");
    setProfile(null);
    setScreen("welcome");
  }

  function handleUpdateProfile() {
    setIsUpdate(true);
    setScreen("onboarding");
  }

  function handleGetStarted() {
    setOnboardingTab("new");
    setScreen("onboarding");
  }

  function handleLogin() {
    setOnboardingTab("returning");
    setScreen("onboarding");
  }

  if (!ready) return null;

  if (screen === "welcome") {
    return (
      <WelcomePage
        onGetStarted={handleGetStarted}
        onLogin={handleLogin}
      />
    );
  }

  if (screen === "onboarding") {
    return (
      <Onboarding
        onComplete={handleProfileReady}
        existingProfile={isUpdate ? profile : null}
        initialTab={onboardingTab}
      />
    );
  }

  return (
    <ChatScreen
      profile={profile}
      onLogout={handleLogout}
      onUpdateProfile={handleUpdateProfile}
    />
  );
}