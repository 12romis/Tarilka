import { render } from "preact";
import { App } from "./app";
import { loadMeta } from "./hooks";
import "./styles.css";
import { initTelegram, tg } from "./tg";

initTelegram();
if (tg) loadMeta(); // довідники вантажимо паралельно з першим рендером
render(<App />, document.getElementById("app")!);
