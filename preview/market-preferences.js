/* Shared, bounded schema for market display preferences. No market data is saved here. */
((root, factory) => {
  const schema = factory();
  if (typeof module === 'object' && module.exports) module.exports = schema;
  if (!root?.AT) return;
  const AT = root.AT;
  AT.marketPreferencesSchema = schema;
  const read = () => schema.sanitize(AT.state?.marketPreferences);
  const commit = value => {if (AT.state) {AT.state.marketPreferences = value; AT.persist?.();}};
  AT.marketPreferences = {
    getList: key => ({...(read().lists[key] || {})}),
    saveList(key, state) {
      if (!schema.validListKey(key)) return;
      const value = read(), list = schema.pickList(state);
      delete value.lists[key]; value.lists[key] = list;
      while (Object.keys(value.lists).length > 100) delete value.lists[Object.keys(value.lists)[0]];
      commit(value);
    },
    resetList(key) {const value = read(); delete value.lists[key]; commit(value);},
    getStudies: () => ({...read().studies}),
    saveStudies(studies) {const value = read(); value.studies = schema.pickStudies(studies); commit(value);}
  };
})(typeof window === 'object' ? window : null, () => {
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const enums = {
    board: ['all','main','star','chinext','bse','index'], pool: ['all','active','gainers','volume'],
    direction: ['all','up','down','flat'], freshness: ['all','latest','older'],
    sort: ['name','category','close','changePercent','return5','return20','amount','volume','volumeRatio5','memberCount'],
    order: ['asc','desc'], size: [15,30,60]
  };
  const booleans = ['above','volume','userSort','compact','filtersOpen','detailsOpen'];
  const text = value => typeof value === 'string' && value.length <= 100 && !value.includes('\0');
  const validListKey = key => typeof key === 'string' && /^(stocks|sectors):[A-Za-z0-9:._-]{1,80}$/.test(key);
  const validField = (key, value) => Object.hasOwn(enums, key) ? enums[key].includes(value) : booleans.includes(key) ? typeof value === 'boolean' : ['query','category'].includes(key) && text(value);
  function pickList(value) {return object(value) ? Object.fromEntries(Object.entries(value).filter(([key, item]) => validField(key, item))) : {};}
  function pickStudies(value) {return object(value) ? Object.fromEntries(Object.entries(value).filter(([key, item]) => ['ma','force','signals','box'].includes(key) && typeof item === 'boolean')) : {};}
  function sanitize(value) {
    const lists = object(value?.lists) ? Object.fromEntries(Object.entries(value.lists).filter(([key]) => validListKey(key)).slice(-100).map(([key, item]) => [key, pickList(item)])) : {};
    return {version: 1, lists, studies: pickStudies(value?.studies)};
  }
  function validate(value) {
    if (!object(value) || value.version !== 1 || Object.keys(value).some(key => !['version','lists','studies'].includes(key)) || !object(value.lists) || !object(value.studies)) throw new Error('行情显示偏好格式无效。');
    if (Object.keys(value.lists).length > 100 || Object.entries(value.lists).some(([key, list]) => !validListKey(key) || !object(list) || Object.entries(list).some(([field, item]) => !validField(field, item)))) throw new Error('行情列表偏好超出允许范围。');
    if (Object.entries(value.studies).some(([key, item]) => !['ma','force','signals','box'].includes(key) || typeof item !== 'boolean')) throw new Error('行情指标偏好格式无效。');
    return true;
  }
  return {sanitize, validate, validListKey, pickList, pickStudies};
});
