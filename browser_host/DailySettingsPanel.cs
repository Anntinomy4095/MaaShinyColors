using System.Drawing;

namespace MaaShinyColors.BrowserHost;

/// <summary>Separate native settings surface. Never sends browser input or opens bridge files.</summary>
internal sealed class DailySettingsPanel : UserControl
{
    private readonly CheckBox _shop = Toggle("商店：当天三项折扣候选");
    private readonly CheckBox _third = Toggle("包括第三项（独立选择）");
    private readonly CheckBox _training = Toggle("S 卡训练偏好");
    private readonly CheckBox _match = Toggle("Match Live skip 偏好");
    private readonly CheckBox _grade = Toggle("Grade Fes 偏好");
    private readonly TextBox _card = new();
    private readonly TextBox _stage = new() { Enabled = false };
    private readonly TextBox _difficulty = new();
    private readonly ComboBox _stageSelection = new() { DropDownStyle = ComboBoxStyle.DropDownList };
    private readonly NumericUpDown _count = Number(20, 1);
    private readonly NumericUpDown[] _budgets = Enumerable.Range(0, 8).Select(_ => Number()).ToArray();
    private readonly DataGridView _offers;
    private readonly Label _status;
    private readonly Label _feedback;
    private readonly Func<string> _readHostStatus;
    private readonly System.Windows.Forms.Timer _statusTimer = new() { Interval = 1000 };

    public DailySettingsPanel(Func<string> readHostStatus)
    {
        _readHostStatus = readHostStatus;
        AutoScaleMode = AutoScaleMode.None;
        BackColor = Color.FromArgb(245, 247, 250);
        var flow = new FlowLayoutPanel { Dock = DockStyle.Fill, AutoScroll = true,
            FlowDirection = FlowDirection.TopDown, WrapContents = false, Padding = new Padding(12) };
        Controls.Add(flow);
        void Add(Control control) { control.Width = 326; control.Margin = new Padding(0, 3, 0, 3); flow.Controls.Add(control); }
        void TextLine(string text, int height = 38) => Add(new Label { Text = text, AutoSize = true,
            MaximumSize = new Size(326, 0), MinimumSize = new Size(0, height) });
        void Field(string text, Control control) { TextLine(text, 20); Add(control); }
        TextLine("日常设置与状态", 25);
        TextLine("这里只保存计划偏好；勾选和预算均不授权游戏输入或消费。默认全关闭。", 48);
        _status = new Label { AutoSize = true, MaximumSize = new Size(326, 0), MinimumSize = new Size(0, 62) }; Add(_status);
        TextLine("当前可使用：截图和设置保存。训练执行待校准；Match 自动参加尚未接通。", 60);
        Add(_training); Field("S 卡身份（完整卡名）", _card);
        TextLine("固定基础铜券 ×1；不会替换券种。", 25);
        Add(_match);
        _stageSelection.Items.AddRange(["最新已验证关卡", "指定 Area/Stage"]);
        _stageSelection.SelectedIndex = 0;
        _stageSelection.SelectedIndexChanged += (_, _) => _stage.Enabled = _stageSelection.SelectedIndex == 1;
        Add(_stageSelection); Field("指定关卡身份", _stage); Field("Match 次数（默认 20；缺证据停止）", _count);
        Add(_grade); Field("Grade 难度 Easy/Normal/Hard（区分大小写；1 次）", _difficulty);
        Add(_shop); Add(_third);
        TextLine("默认按位次购买 any_daily_discount（当天第1、2项，名称须在卡片与弹窗一致）；第三项仅在勾选后导出。只用マニー，不能将普通货架当折扣。", 64);
        _offers = new DataGridView { Height = 150, AllowUserToAddRows = false, AllowUserToDeleteRows = false,
            RowHeadersVisible = false, AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.None };
        _offers.Columns.Add(new DataGridViewTextBoxColumn { Name = "rank", HeaderText = "项", Width = 28, ReadOnly = true });
        _offers.Columns.Add(new DataGridViewTextBoxColumn { Name = "id", HeaderText = "商品身份", Width = 100 });
        _offers.Columns.Add(new DataGridViewComboBoxColumn { Name = "type", HeaderText = "类型", Width = 130,
            DisplayMember = "Label", ValueMember = "Value", DataSource = new[]
            {
                new { Label = "基础训练券", Value = "basic_training_ticket" },
                new { Label = "Fes参加券", Value = "fes_entry_ticket" },
                new { Label = "普通物品", Value = "ordinary_item" },
            } });
        _offers.Columns.Add(new DataGridViewTextBoxColumn { Name = "quantity", HeaderText = "数量", Width = 55 });
        _offers.Columns.Add(new DataGridViewTextBoxColumn { Name = "price", HeaderText = "最高单价", Width = 80 });
        for (var rank = 1; rank <= 3; rank++)
        { _offers.Rows.Add(rank, DailySettingsConfiguration.AnyDailyDiscount, "ordinary_item", 1, rank < 3 ? "10" : ""); }
        _offers.DataError += (_, args) => { args.ThrowException = false; };
        Add(_offers);
        string[] labels = ["总金币预算", "总基础铜券预算", "总 Fes 参加权预算（不含恢复券）", "商店金币预算", "商店金币保留额", "训练铜券预算", "Match 参加权预算", "Grade 参加权预算"];
        for (var index = 0; index < labels.Length; index++) { Field(labels[index], _budgets[index]); }
        TextLine("付费货币、库存替代、恢复券均禁止。免费十连请人工确认并抽取。", 46);
        var save = new Button { Text = "保存偏好到新配置副本", Height = 32 };
        save.Click += (_, _) => SaveCopy(); Add(save);
        var refresh = new Button { Text = "刷新宿主只读状态", Height = 30 };
        refresh.Click += (_, _) => RefreshStatus(); Add(refresh);
        _feedback = new Label { AutoSize = true, MaximumSize = new Size(326, 0), MinimumSize = new Size(0, 80) }; Add(_feedback);
        RefreshStatus();
        _statusTimer.Tick += (_, _) => RefreshStatus();
        _statusTimer.Start();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) { _statusTimer.Dispose(); }
        base.Dispose(disposing);
    }

    private void RefreshStatus() => _status.Text = "宿主状态（不代表日常成功）：\n" + _readHostStatus();

    private void SaveCopy()
    {
        try
        {
            _offers.EndEdit();
            var configuration = new DailySettingsConfiguration
            {
                ShopEnabled = _shop.Checked, IncludeThird = _third.Checked, TrainingEnabled = _training.Checked,
                MatchEnabled = _match.Checked, GradeEnabled = _grade.Checked, CardId = _card.Text,
                StageId = _stage.Text, DifficultyId = _difficulty.Text, LatestStage = _stageSelection.SelectedIndex == 0,
                MatchCount = (int)_count.Value, Coins = (int)_budgets[0].Value, BronzeTickets = (int)_budgets[1].Value,
                FesRights = (int)_budgets[2].Value, ShopBudget = (int)_budgets[3].Value, CoinReserve = (int)_budgets[4].Value,
                TrainingBudget = (int)_budgets[5].Value, MatchBudget = (int)_budgets[6].Value, GradeBudget = (int)_budgets[7].Value,
            };
            foreach (DataGridViewRow row in _offers.Rows)
            {
                var id = Convert.ToString(row.Cells["id"].Value)?.Trim() ?? "";
                if (id.Length == 0 || ((int)row.Cells["rank"].Value! == 3 && !_third.Checked)) { continue; }
                if (!int.TryParse(Convert.ToString(row.Cells["quantity"].Value), out var quantity)
                    || !int.TryParse(Convert.ToString(row.Cells["price"].Value), out var price))
                { throw new ArgumentException("商品数量及最高单价须填写正整数。"); }
                configuration.Offers.Add(new((int)row.Cells["rank"].Value!, id,
                    Convert.ToString(row.Cells["type"].Value) ?? "", quantity, price));
            }
            _ = configuration.ToJson();
            using var dialog = new SaveFileDialog { Title = "保存新的日常偏好副本（不覆盖已有文件）",
                Filter = "JSON 配置 (*.json)|*.json", FileName = "daily-preferences.json", OverwritePrompt = false };
            if (dialog.ShowDialog(this) != DialogResult.OK) { return; }
            configuration.SaveNewCopy(dialog.FileName);
            _feedback.Text = "偏好已保存；未发送游戏输入、未授权消费。\n" + dialog.FileName;
        }
        catch (Exception exception) { _feedback.Text = "保存停止：" + exception.Message; }
    }

    private static CheckBox Toggle(string text) => new() { Text = text, Height = 28, Checked = false };
    private static NumericUpDown Number(int value = 0, int minimum = 0) => new()
        { Minimum = minimum, Maximum = int.MaxValue, Value = value, ThousandsSeparator = true };
}
