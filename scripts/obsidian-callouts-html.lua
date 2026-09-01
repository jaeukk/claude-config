-- obsidian-callouts-html.lua -- pandoc filter for the HTML/Chromium export.
-- Rebuilds the DOM Obsidian itself produces for a callout,
--   <div class="callout" data-callout="warning">
--     <div class="callout-title"><div class="callout-title-inner">…</div></div>
--     <div class="callout-content">…</div>
--   </div>
-- so that Obsidian's own app.css styles it exactly as in the app.

-- callout type -> lucide icon, taken from app.css itself (see
-- obsidian-assets/callout-icons.txt, regenerated when Obsidian updates).
local assets = (PANDOC_SCRIPT_FILE or ''):gsub('[^/]*$', '') .. 'obsidian-assets/'
local icon_of, svg_cache = {}, {}
do
  local f = io.open(assets .. 'callout-icons.txt')
  if f then
    for line in f:lines() do
      local k, v = line:match('^(%S+)\t(%S+)$')
      if k then icon_of[k] = v end
    end
    f:close()
  end
end

local function icon_svg(kind)
  local name = icon_of[kind] or icon_of['__default__']
  if not name then return nil end
  if svg_cache[name] == nil then
    local f = io.open(assets .. 'icons/' .. name .. '.svg')
    svg_cache[name] = f and f:read('a'):gsub('<svg ',
      '<svg class="svg-icon lucide-' .. name .. '" ') or false
    if f then f:close() end
  end
  return svg_cache[name] or nil
end

function BlockQuote(bq)
  local head = bq.content[1]
  if not head or (head.t ~= 'Para' and head.t ~= 'Plain') then return nil end
  local first = head.content[1]
  if not first or first.t ~= 'Str' then return nil end
  local kind, fold = first.text:match('^%[!([%w%-]+)%]([+-]?)$')
  if not kind then return nil end

  local title, rest, seen = {}, {}, false
  for i = 2, #head.content do
    local el = head.content[i]
    if not seen and (el.t == 'SoftBreak' or el.t == 'LineBreak') then
      seen = true
    elseif seen then
      rest[#rest + 1] = el
    elseif #title > 0 or el.t ~= 'Space' then
      title[#title + 1] = el
    end
  end
  if #title == 0 then
    title = {pandoc.Str(kind:sub(1, 1):upper() .. kind:sub(2))}
  end

  local body = {}
  if #rest > 0 then body[#body + 1] = pandoc.Para(rest) end
  for i = 2, #bq.content do body[#body + 1] = bq.content[i] end

  local titleParts = {}
  local svg = icon_svg(kind:lower())
  if svg then
    titleParts[#titleParts + 1] =
      pandoc.Div(pandoc.RawBlock('html', svg), {class = 'callout-icon'})
  end
  titleParts[#titleParts + 1] =
    pandoc.Div(pandoc.Plain(title), {class = 'callout-title-inner'})
  local titleEl = pandoc.Div(titleParts, {class = 'callout-title'})
  local classes = {'callout'}
  if fold ~= '' then classes[#classes + 1] = 'is-collapsible' end
  return pandoc.Div(
    {titleEl, pandoc.Div(body, {class = 'callout-content'})},
    pandoc.Attr('', classes, {['data-callout'] = kind:lower()}))
end
