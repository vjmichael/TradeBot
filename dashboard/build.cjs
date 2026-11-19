'use strict';
// Copy pinned local npm distributions; no browser CDN or remotely loaded code.
const fs = require('node:fs');
const path = require('node:path');
const expected = {'react':'18.3.1','react-dom':'18.3.1','scheduler':'0.23.2','loose-envify':'1.4.0','js-tokens':'4.0.0'};
for (const [name,version] of Object.entries(expected)) {
  const metadata = require(path.join(__dirname,'node_modules',name,'package.json'));
  if(metadata.version!==version)throw Error('Unexpected dependency version: '+name);
}
const vendor=path.join(__dirname,'vendor'); fs.mkdirSync(vendor,{recursive:true});
for (const [name,file] of [['react','react.production.min.js'],['react-dom','react-dom.production.min.js']]) {
  fs.copyFileSync(path.join(__dirname,'node_modules',name,'umd',file),path.join(vendor,name+'.js'));
  fs.copyFileSync(path.join(__dirname,'node_modules',name,'LICENSE'),path.join(vendor,name+'.LICENSE.txt'));
}
console.log('Built pinned React dashboard assets');
