-- obsidian-callouts.lua -- pandoc filter for the headless note export.
-- Turns Obsidian callouts  "> [!type] Title / > body"  into tcolorbox
-- environments (see the \obscallout definition injected by
-- export_notconfirmed_to_pdf.sh).  Anything that is not a callout is left
-- as an ordinary blockquote.

local color = {
  note = 'cbBlue',    info = 'cbBlue',    todo = 'cbBlue',   formula = 'cbBlue',
  abstract = 'cbCyan', summary = 'cbCyan', tldr = 'cbCyan',
  tip = 'cbTeal',     hint = 'cbTeal',    important = 'cbTeal', define = 'cbTeal',
  success = 'cbGreen', check = 'cbGreen', done = 'cbGreen',
  question = 'cbAmber', help = 'cbAmber', faq = 'cbAmber',
  warning = 'cbOrange', caution = 'cbOrange', attention = 'cbOrange',
  failure = 'cbRed',  fail = 'cbRed',     missing = 'cbRed',
  danger = 'cbRed',   error = 'cbRed',    bug = 'cbRed',
  example = 'cbPurple',
  quote = 'cbGray',   cite = 'cbGray',    remark = 'cbGray', remarks = 'cbGray',
}

local function latex(inlines)
  return pandoc.write(pandoc.Pandoc({pandoc.Plain(inlines)}), 'latex')
end

function BlockQuote(bq)
  local head = bq.content[1]
  if not head or (head.t ~= 'Para' and head.t ~= 'Plain') then return nil end
  local first = head.content[1]
  if not first or first.t ~= 'Str' then return nil end
  local kind = first.text:match('^%[!([%w%-]+)%][+-]?$')
  if not kind then return nil end

  -- title = rest of the first line; body = everything after the line break
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

  local body = {}
  if #rest > 0 then body[#body + 1] = pandoc.Para(rest) end
  for i = 2, #bq.content do body[#body + 1] = bq.content[i] end

  if #title == 0 then
    title = {pandoc.Str(kind:sub(1, 1):upper() .. kind:sub(2))}
  end

  local open = pandoc.RawBlock('latex', string.format(
    '\\begin{obscallout}{%s}{%s}', color[kind:lower()] or 'cbGray', latex(title)))
  local out = {open}
  for _, b in ipairs(body) do out[#out + 1] = b end
  out[#out + 1] = pandoc.RawBlock('latex', '\\end{obscallout}')
  return out
end
