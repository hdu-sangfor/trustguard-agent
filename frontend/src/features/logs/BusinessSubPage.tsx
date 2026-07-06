/**
 * 业务页面包：在宿主项目中作为子路由组件使用。
 * 本文件会引入全局样式（Tailwind / 主题），宿主只需渲染此组件即可。
 */
import './styles/index.css';
import { Home } from './terminal/Home';

export function BusinessSubPage() {
  return <Home />;
}

export default BusinessSubPage;
