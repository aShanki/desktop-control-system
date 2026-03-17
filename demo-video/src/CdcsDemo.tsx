import {
  AbsoluteFill,
  Img,
  Sequence,
  interpolate,
  useCurrentFrame,
  staticFile,
  spring,
  useVideoConfig,
} from "remotion";

/* ── Shared animation helpers ────────────────────────────────────── */

const FadeIn: React.FC<{
  children: React.ReactNode;
  delay?: number;
  y?: number;
}> = ({ children, delay = 0, y = 30 }) => {
  const frame = useCurrentFrame();
  const f = Math.max(0, frame - delay);
  const opacity = interpolate(f, [0, 12], [0, 1], {
    extrapolateRight: "clamp",
  });
  const translateY = interpolate(f, [0, 12], [y, 0], {
    extrapolateRight: "clamp",
  });
  return (
    <div style={{ opacity, transform: `translateY(${translateY}px)` }}>
      {children}
    </div>
  );
};

const Terminal: React.FC<{ lines: string[]; delay?: number }> = ({
  lines,
  delay = 0,
}) => {
  const frame = useCurrentFrame();
  return (
    <div className="bg-zinc-950 border border-zinc-800 rounded-xl p-6 font-mono text-sm shadow-2xl w-full max-w-2xl">
      <div className="flex gap-2 mb-4">
        <div className="w-3 h-3 rounded-full bg-red-500" />
        <div className="w-3 h-3 rounded-full bg-yellow-500" />
        <div className="w-3 h-3 rounded-full bg-green-500" />
      </div>
      {lines.map((line, i) => {
        const lineDelay = delay + i * 6;
        const opacity = interpolate(
          frame,
          [lineDelay, lineDelay + 4],
          [0, 1],
          { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
        );
        return (
          <div key={i} style={{ opacity }} className="text-zinc-300 leading-7">
            {line.startsWith("$") ? (
              <>
                <span className="text-emerald-400">$ </span>
                <span className="text-white">{line.slice(2)}</span>
              </>
            ) : line.startsWith("#") ? (
              <span className="text-zinc-600">{line}</span>
            ) : (
              <span className="text-zinc-400">{line}</span>
            )}
          </div>
        );
      })}
    </div>
  );
};

const UseCaseCard: React.FC<{
  icon: string;
  title: string;
  desc: string;
  color: string;
  delay?: number;
}> = ({ icon, title, desc, color, delay = 0 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const scale = spring({ frame: frame - delay, fps, config: { damping: 12 } });
  const opacity = interpolate(frame, [delay, delay + 8], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{ opacity, transform: `scale(${scale})` }}
      className="bg-zinc-900/80 border border-zinc-800 rounded-2xl p-8 flex flex-col gap-3 w-72"
    >
      <span className="text-5xl">{icon}</span>
      <h3 className={`text-2xl font-bold ${color}`}>{title}</h3>
      <p className="text-zinc-500 text-lg leading-relaxed">{desc}</p>
    </div>
  );
};

/* ── Scene 1: Title ──────────────────────────────────────────────── */
const TitleScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const scale = spring({ frame, fps, config: { damping: 15, mass: 0.8 } });
  const opacity = interpolate(frame, [0, 10], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill className="bg-black flex flex-col items-center justify-center">
      <div style={{ opacity, transform: `scale(${scale})` }} className="text-center">
        <h1 className="text-9xl font-black text-white tracking-tighter">DCS</h1>
        <div className="mt-4 h-1.5 w-32 mx-auto bg-gradient-to-r from-blue-500 to-cyan-400 rounded-full" />
        <FadeIn delay={10}>
          <p className="text-4xl text-zinc-400 mt-6 font-light">
            Desktop Control System
          </p>
        </FadeIn>
      </div>
      <FadeIn delay={20}>
        <p className="text-xl text-zinc-600 mt-16 font-mono tracking-wide">
          Hidden desktop automation for AI agents on Windows
        </p>
      </FadeIn>
    </AbsoluteFill>
  );
};

/* ── Scene 2: The Problem ────────────────────────────────────────── */
const ProblemScene: React.FC = () => {
  return (
    <AbsoluteFill className="bg-black flex flex-col items-center justify-center px-24">
      <FadeIn>
        <p className="text-3xl text-zinc-600 font-mono mb-6">The problem</p>
      </FadeIn>
      <FadeIn delay={8}>
        <h2 className="text-6xl font-bold text-white text-center leading-tight max-w-4xl">
          AI agents can edit code, but they{" "}
          <span className="text-red-400">can't click buttons.</span>
        </h2>
      </FadeIn>
      <FadeIn delay={22}>
        <p className="text-2xl text-zinc-500 mt-10 text-center max-w-3xl">
          GUI apps, legacy tools, simulators — all out of reach. Until now.
        </p>
      </FadeIn>
    </AbsoluteFill>
  );
};

/* ── Scene 3: How It Works ───────────────────────────────────────── */
const HowItWorksScene: React.FC = () => {
  return (
    <AbsoluteFill className="bg-black flex flex-col items-center justify-center px-20 gap-10">
      <FadeIn>
        <p className="text-3xl text-zinc-600 font-mono mb-2">How it works</p>
      </FadeIn>
      <Terminal
        delay={8}
        lines={[
          "$ python sandbox_ctl.py create session-0",
          '  {"ok": true, "desktop": "session-0"}',
          "",
          '$ python sandbox_ctl.py launch session-0 "notepad.exe"',
          '  {"ok": true, "hwnd": 264616}',
          "",
          "$ python sandbox_ctl.py screenshot session-0",
          '  {"ok": true, "width": 1920, "height": 1080}',
          "",
          "$ python sandbox_ctl.py click session-0 400 300",
          '  {"ok": true, "method": "postmessage"}',
        ]}
      />
      <FadeIn delay={60}>
        <p className="text-xl text-zinc-600 font-mono">
          Hidden desktop &middot; Named pipes &middot; PostMessage &middot; PrintWindow
        </p>
      </FadeIn>
    </AbsoluteFill>
  );
};

/* ── Scene 4: Use Cases Grid ─────────────────────────────────────── */
const UseCasesScene: React.FC = () => {
  return (
    <AbsoluteFill className="bg-black flex flex-col items-center justify-center px-16">
      <FadeIn>
        <p className="text-3xl text-zinc-600 font-mono mb-10">Use cases</p>
      </FadeIn>
      <div className="flex gap-6">
        <UseCaseCard
          icon="🖧"
          title="Network Labs"
          desc="Build topologies, configure devices, run commands in Cisco Packet Tracer"
          color="text-blue-400"
          delay={6}
        />
        <UseCaseCard
          icon="🧪"
          title="GUI Testing"
          desc="Automate desktop app test flows — click, type, screenshot, assert"
          color="text-cyan-400"
          delay={12}
        />
        <UseCaseCard
          icon="📋"
          title="Legacy Apps"
          desc="Fill forms, export reports, drive apps that have no API or CLI"
          color="text-purple-400"
          delay={18}
        />
        <UseCaseCard
          icon="⚡"
          title="Parallel Sessions"
          desc="Run multiple isolated desktops simultaneously — no conflicts"
          color="text-amber-400"
          delay={24}
        />
      </div>
    </AbsoluteFill>
  );
};

/* ── Scene 5: Live Demo ──────────────────────────────────────────── */
const LiveDemoScene: React.FC = () => {
  const frame = useCurrentFrame();
  const imgOpacity = interpolate(frame, [0, 15], [0, 1], {
    extrapolateRight: "clamp",
  });
  const labelOpacity = interpolate(frame, [10, 25], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill className="bg-black flex items-center justify-center gap-12 px-16">
      <div style={{ opacity: imgOpacity }} className="flex-shrink-0">
        <Img
          src={staticFile("agent-logs.png")}
          className="rounded-xl shadow-2xl shadow-blue-500/20 border border-zinc-800"
          style={{ width: 750, height: "auto" }}
        />
      </div>
      <div style={{ opacity: labelOpacity }} className="max-w-lg">
        <p className="text-blue-400 font-mono text-lg mb-3">Real output</p>
        <h2 className="text-4xl font-bold text-white leading-snug">
          Agent running on a hidden desktop
        </h2>
        <p className="text-zinc-500 text-xl mt-4 leading-relaxed">
          Screenshots, clicks, keystrokes — all dispatched via named pipes.
          Your visible desktop is never touched.
        </p>
      </div>
    </AbsoluteFill>
  );
};

/* ── Scene 6: Outro ──────────────────────────────────────────────── */
const OutroScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const scale = spring({ frame, fps, config: { damping: 15 } });
  const opacity = interpolate(frame, [0, 12], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill className="bg-black flex flex-col items-center justify-center">
      <div style={{ opacity, transform: `scale(${scale})` }} className="text-center">
        <h2 className="text-7xl font-black text-white">
          Desktop control,{" "}
          <span className="bg-gradient-to-r from-blue-400 to-cyan-400 bg-clip-text text-transparent">
            solved.
          </span>
        </h2>
        <FadeIn delay={12}>
          <div className="flex items-center justify-center gap-8 mt-10">
            <span className="text-2xl text-zinc-500 font-mono">pip install pywin32 pillow</span>
            <span className="text-zinc-700">|</span>
            <span className="text-2xl text-zinc-500 font-mono">Windows 10/11</span>
          </div>
        </FadeIn>
      </div>
    </AbsoluteFill>
  );
};

/* ── Main composition ────────────────────────────────────────────── */
export const CdcsDemo: React.FC = () => {
  return (
    <AbsoluteFill className="bg-black">
      {/* 0–3s: Title */}
      <Sequence from={0} durationInFrames={90}>
        <TitleScene />
      </Sequence>

      {/* 3–6.2s: Problem */}
      <Sequence from={90} durationInFrames={95}>
        <ProblemScene />
      </Sequence>

      {/* 6.2–10.2s: How it works (terminal) */}
      <Sequence from={185} durationInFrames={120}>
        <HowItWorksScene />
      </Sequence>

      {/* 10.2–13.5s: Use cases grid */}
      <Sequence from={305} durationInFrames={100}>
        <UseCasesScene />
      </Sequence>

      {/* 13.5–16.3s: Agent logs */}
      <Sequence from={405} durationInFrames={85}>
        <LiveDemoScene />
      </Sequence>

    </AbsoluteFill>
  );
};
