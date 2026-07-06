import Header from "@/shared/components/Header";

const IndexPage = () => (
  <div style={{ minHeight: "100vh", background: "var(--tg-page-bg)" }}>
    <Header />
    <main aria-label="首页内容" style={{ minHeight: "calc(100vh - 64px)", paddingTop: 64 }} />
  </div>
);

export default IndexPage;
