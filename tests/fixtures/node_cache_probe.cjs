// Enable Node's compile cache, then load a module it would cache.
require("node:module").enableCompileCache?.();
require("./node_cache_module.cjs");
