export type NavigationItem = {
  label: string;
  href: string;
  requiresAuth: boolean;
};

export const NAVIGATION_ITEMS: readonly NavigationItem[] = [
  { label: "首页", href: "/", requiresAuth: false },
  { label: "语料资源", href: "/corpora/", requiresAuth: true },
  { label: "检索中心", href: "/corpora/?tool=kwic", requiresAuth: true },
  { label: "用户语料", href: "/corpora/mine/", requiresAuth: true },
  { label: "对齐工具", href: "/corpora/?tool=parallel", requiresAuth: true },
  { label: "统计分析", href: "/corpora/?tool=statistics", requiresAuth: true },
  { label: "帮助中心", href: "#platform-guide", requiresAuth: false },
];
