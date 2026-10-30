import {readFile} from 'node:fs/promises';
import {isAbsolute, resolve} from 'node:path';

export async function loadConfig(file=process.env.WIKI_CONFIG) {
  if(!file||!isAbsolute(file))throw Error('WIKI_CONFIG must be an absolute private JSON path');
  const config=JSON.parse(await readFile(file,'utf8'));
  config.paths??={};config.couchdb??={};
  if(config.schema!==1)throw Error('Unsupported private configuration schema');
  for(const name of ['state','registry','meeting_data','upstream_dist']) {
    const env=process.env['WIKI_PATH_'+name.toUpperCase()];
    if(env)config.paths[name]=env;
    if(!isAbsolute(config.paths?.[name]??''))throw Error('Missing absolute path: '+name);
    config.paths[name]=resolve(config.paths[name]);
  }
  for(const name of ['url','user','password','database','passphrase']) {
    const env=process.env['WIKI_COUCHDB_'+name.toUpperCase()];
    if(env)config.couchdb[name]=env;
    if(typeof config.couchdb?.[name]!=='string'||!config.couchdb[name])throw Error('Missing CouchDB field: '+name);
  }
  const url=new URL(config.couchdb.url);
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw Error('Invalid CouchDB endpoint');
  if(!config.http||!Number.isInteger(config.http.port)||config.http.port<1024||config.http.port>65535)throw Error('Invalid HTTP port');
  if(!['127.0.0.1','::1'].includes(config.http.bind))throw Error('Host must bind loopback; use the authenticated TLS proxy');
  return config;
}
