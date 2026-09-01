import { Empty } from "antd";

interface ComingSoonProps {
  title: string;
  /** 一句话说明该模块规划中的能力。 */
  description?: string;
}

/** 编辑端三模块（Agent 工作台 / 工作流编排 / 知识库）的统一占位页。 */
export default function ComingSoon({ title, description }: ComingSoonProps) {
  return (
    <div className="page-center">
      <div className="coming-soon">
        <div className="eyebrow">PLANNED</div>
        <h2 className="font-display">{title}</h2>
        <p>{description ?? "该模块已纳入编辑端规划，当前切片不展开实现。"}</p>
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={false} />
      </div>
    </div>
  );
}
