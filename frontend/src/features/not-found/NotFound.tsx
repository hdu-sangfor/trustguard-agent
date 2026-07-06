import { useLocation, useNavigate } from "react-router-dom";
import Header from "@/shared/components/Header";

const NotFound = () => {
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <div style={{
      minHeight: "100vh",
      background: "linear-gradient(180deg, #0f172a 0%, #1e1b4b 50%, #0f172a 100%)",
      display: "flex",
      flexDirection: "column",
    }}>
      <Header />
      <div style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 20,
        fontFamily: "'Courier New', monospace",
        padding: "80px 24px 48px",
      }}>
        <div style={{
          fontSize: "clamp(4rem, 12vw, 9rem)",
          fontWeight: 900,
          letterSpacing: "0.12em",
          color: "rgba(34,211,238,0.18)",
          lineHeight: 1,
          userSelect: "none",
        }}>404</div>
        <div style={{ fontSize: "1rem", fontWeight: 700, color: "#7dd3fc", letterSpacing: "0.2em", textTransform: "uppercase" }}>
          路径未找到
        </div>
        <div style={{
          fontSize: "0.8rem", color: "rgba(100,116,139,0.9)", fontFamily: "monospace",
          background: "rgba(2,8,20,0.7)", border: "1px solid rgba(34,211,238,0.15)",
          borderRadius: 8, padding: "8px 16px", maxWidth: 480, textAlign: "center", wordBreak: "break-all",
        }}>
          {location.pathname}
        </div>
        <div style={{ display: "flex", gap: 12, marginTop: 8, flexWrap: "wrap", justifyContent: "center" }}>
          <button
            type="button"
            onClick={() => navigate("/")}
            style={{
              padding: "10px 24px", borderRadius: 9, fontWeight: 700, fontSize: "0.85rem",
              border: "1px solid rgba(34,211,238,0.5)", background: "rgba(2,8,20,0.7)",
              color: "#a5f3fc", cursor: "pointer", letterSpacing: "0.04em",
              fontFamily: "monospace",
            }}
          >
            返回首页
          </button>
          <button
            type="button"
            onClick={() => navigate(-1)}
            style={{
              padding: "10px 24px", borderRadius: 9, fontWeight: 700, fontSize: "0.85rem",
              border: "1px solid rgba(71,85,105,0.6)", background: "rgba(2,8,20,0.7)",
              color: "#64748b", cursor: "pointer", letterSpacing: "0.04em",
              fontFamily: "monospace",
            }}
          >
            返回上一页
          </button>
        </div>
      </div>
    </div>
  );
};

export default NotFound;
