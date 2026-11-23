import { render } from "preact";
import { App } from "./app";
import { loadMeta } from "./hooks";
import { initPwa } from "./pwa";
import "./styles.css";
import { initTelegram, tg } from "./tg";

initTelegram();
initPwa();
if (tg) loadMeta(); // довідники вантажимо паралельно з першим рендером
render(<App />, document.getElementById("app")!);
