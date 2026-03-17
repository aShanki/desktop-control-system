import { Composition } from "remotion";
import { CdcsDemo } from "./CdcsDemo";
import "./style.css";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="CdcsDemo"
      component={CdcsDemo}
      durationInFrames={490}
      fps={30}
      width={1920}
      height={1080}
    />
  );
};
