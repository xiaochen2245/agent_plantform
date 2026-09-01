import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * AI 回复的 markdown 渲染（全宽文档式排版，设计稿约定）。
 * 样式在 styles/markdown.css（墨青 token：#F1F5F9 代码底 / 8px 圆角 / hairline 描边）。
 * 流式期间直接渲染部分 markdown；未闭合代码块由 react-markdown 容忍。
 */
export default function Markdown({ content }: { content: string }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: (props) => <a {...props} target="_blank" rel="noreferrer" />,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
