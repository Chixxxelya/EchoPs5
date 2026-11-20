#ifndef CONFIGURATION_EDIT_DIALOG_H
#define CONFIGURATION_EDIT_DIALOG_H

#include <QDialog>
#include <QList>
#include <QString>
#include <QStringList>
class QByteArray;
class QCheckBox;
class QGroupBox;
class QLineEdit;
class QListWidget;
class QMoveEvent;
class QResizeEvent;
class QSpinBox;
class QToolButton;
class QSettings;
class QWidget;

class Configuration;
namespace Ui {
class ConfigurationEditDialog;
} // namespace Ui

class ConfigurationEditDialog: public QDialog {
	Q_OBJECT

public:
	explicit ConfigurationEditDialog(Configuration& info, QWidget* parent = nullptr);
	~ConfigurationEditDialog() override;

	static void WriteSettings(QSettings& s);
	static void ReadSettings(QSettings& s);

	void                      SetGlobalSettings(const QStringList& dirs, const QString& addons_dir);
	[[nodiscard]] QString     GetAddonsDirectory() const;
	[[nodiscard]] QStringList GetGameDirectories() const;

signals:
	void PreviewControllerColor(const QString& color);
	void ImportGameSettings();
	void ExportGameSettings();

private:
	Ui::ConfigurationEditDialog* m_ui = nullptr;
	Configuration&               m_info;
	QGroupBox*                   m_game_dirs_group        = nullptr;
	QListWidget*                 m_game_dirs_list         = nullptr;
	QLineEdit*                   m_addons_dir_edit        = nullptr;
	QToolButton*                 m_remove_game_dir_button = nullptr;
	// The performance panel: one checkbox per switch group, three numeric switches.
	QGroupBox*                   m_perf_group      = nullptr;
	QList<QString>               m_perf_tokens;
	QList<QCheckBox*>            m_perf_checks;
	QSpinBox*                    m_perf_window     = nullptr;
	QSpinBox*                    m_perf_hot        = nullptr;
	QSpinBox*                    m_perf_merge      = nullptr;
	bool                         m_global_settings        = false;
	bool                         m_geometry_restored      = false;

protected:
	void Init(const Configuration& info);
	void InitPerformanceGroup();
	void InitGameDirectories();
	void AddGameDirectoryItem(const QString& dir);
	void MakeTabsScrollable();
	void GrowToFitContents();
	void ShowTab(QWidget* page);

	void moveEvent(QMoveEvent* event) override;
	void resizeEvent(QResizeEvent* event) override;

	static QByteArray g_last_geometry;

	/*slots:*/

	void save();
	void clear();
	void add_game_directory();
	void remove_selected_game_directories();
	void update_game_directory_buttons();
};

#endif // CONFIGURATION_EDIT_DIALOG_H
