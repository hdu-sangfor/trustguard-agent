import type { ReactNode } from "react";
import "./PageTitle.css";

type PageTitleProps = {
  eyebrow: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
};

export default function PageTitle({ eyebrow, title, description, actions }: PageTitleProps) {
  return (
    <section className="tg-page-title">
      <div>
        <div className="tg-page-title-eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="tg-page-title-actions">{actions}</div>}
    </section>
  );
}
