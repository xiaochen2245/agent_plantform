interface LogomarkProps {
  size?: number;
}

/** 产品标识：圆环 + 节点（与设计稿 v3 一致）。 */
export default function Logomark({ size = 36 }: LogomarkProps) {
  return (
    <div className="logomark" style={{ width: size, height: size }} aria-hidden="true">
      <svg width={size * 0.55} height={size * 0.55} viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="3" fill="currentColor" />
        <circle cx="12" cy="12" r="8" stroke="currentColor" strokeOpacity="0.55" strokeWidth="1.4" />
      </svg>
    </div>
  );
}
