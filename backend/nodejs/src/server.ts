import { createApp } from './app.js';
import { loadConfig } from './core/config.js';

const config = loadConfig();
const app = createApp(config);

app.listen(config.port, () => {
  console.log(`Node backend listening on http://localhost:${config.port}`);
});
