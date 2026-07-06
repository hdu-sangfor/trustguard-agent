import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import Header from "@/shared/components/Header";
import { BusinessSubPage } from "./BusinessSubPage";
import { useAppSession } from "@/shared/context/AppSessionContext";

const LogsPage = () => {
  const { loggedIn } = useAppSession();
  const navigate = useNavigate();

  useEffect(() => {
    const style = document.createElement('style');
    style.textContent = `
      html, body {
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
          width: 100vw !important;
          height: 100vh !important;
      }
      html::-webkit-scrollbar, body::-webkit-scrollbar {
        display: none !important;
      }
      html {
        scrollbar-width: none !important;
      }
    `;
    document.head.appendChild(style);
    document.body.classList.add("no-page-scroll", "no-scrollbar");

    return () => {
      document.head.removeChild(style);
      document.body.classList.remove("no-page-scroll", "no-scrollbar");
    };
  }, []);

  useEffect(() => {
    if (!loggedIn) {
      toast.error("请先登录");
      localStorage.setItem("sentinel_login_redirect", "/logs");
      navigate("/login", { replace: true });
    }
  }, [loggedIn, navigate]);

  if (!loggedIn) return null;

  return (
      <div style={{
        width: "100vw",
        height: "100vh",
        background: "var(--tg-page-bg)",
        position: "relative",
        margin: 0,
        padding: 0,
      }}>
        <Header />

        <div style={{
          position: "absolute",
          top: "60px",
          left: 0,
          right: 0,
          bottom: 0,
          background: "var(--tg-page-bg)",
          overflow: "hidden",
        }}>
          <BusinessSubPage />
        </div>
      </div>
  );
};

export default LogsPage;
